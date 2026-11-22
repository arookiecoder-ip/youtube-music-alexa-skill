"""Exercise the production Echo adapter with account-scoped state and a fake Amazon API."""
import ast
import contextvars
from pathlib import Path
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
from test_playback_lock_contract import ProductionLock


class EchoAdapterTests(unittest.TestCase):
    def setUp(self):
        self.identity=contextvars.ContextVar('identity',default='owner')
        self.players={};self.outputs={};self.updates=[];self.sent=[]
        test=self
        class Player:
            def data(self):return test.players.setdefault(test.identity.get(),{})
            def get(self,*args):return self.data().get(*args)
            def __getitem__(self,key):return self.data()[key]
            def __setitem__(self,key,value):self.data()[key]=value
            def pop(self,*args):return self.data().pop(*args)
            def update(self,*args,**kwargs):self.data().update(*args,**kwargs)
        self.player=Player()
        self.state=dict(queue=[dict(video_id='aaaaaaaaaaa',entry_id='first',title='First',duration_ms=10000),
                               dict(video_id='bbbbbbbbbbb',entry_id='second',title='Second',duration_ms=20000)],
                        queue_index=0,queue_version=1,video_id='aaaaaaaaaaa',entry_id='first',duration_ms=10000,
                        playing=True,position_ms=1000,seek_revision=0,transport_revision=1,selected=['echo-a','echo-b'])
        def output():return self.outputs.setdefault(self.identity.get(),dict(playback_output='phone',output_serial='',output_token='original'))
        def claim(serial,**kwargs):output().update(playback_output='alexa',output_serial=serial,output_token='new')
        lock=ProductionLock()
        def update(**kwargs):
            with lock:
                self.updates.append((self.identity.get(),kwargs));self.player.update(**kwargs)
        def play(serial,video,offset):self.sent.append((self.identity.get(),serial,video,offset))
        self.ns=dict(_PLAYER_IDENTITY=self.identity,_np_lock=lock,_now_playing=self.player,
            _playback_output=SimpleNamespace(snapshot=output),_claim_alexa_output=Mock(side_effect=claim),
            _notify_sse=Mock(),_update_now_playing=update,_ensure_audio_ready_for_play=Mock(),_arm_play=Mock(),_jam_token_valid=lambda token:True,
            _jam_rooms=SimpleNamespace(transact=lambda token:self.state),JamConflict=ValueError,
            alexa_remote=SimpleNamespace(remote=SimpleNamespace(play_video_id=play,command=Mock(return_value=None))),
            time=SimpleNamespace(time=lambda:100,monotonic=lambda:100),_record_volume_state=Mock(),
            _np_snapshot=lambda serial,retain_errors:dict(self.player.data()),_volume_by_serial={},
            _jam_echo_driver_lock=threading.Lock(),_jam_volume_last_poll={},_bg_thread=Mock(),_refresh_volume=Mock())
        source=Path(__file__).resolve().parents[1]/'server.py'
        nodes=[n for n in ast.parse(source.read_text()).body if isinstance(n,ast.FunctionDef) and n.name in ('_jam_send_echo','_jam_echo_snapshot')]
        exec(compile(ast.Module(body=nodes,type_ignores=[]),str(source),'exec'),self.ns)
        self.a=dict(identity='user:alice',serial='A',device='echo-a')
        self.b=dict(identity='user:alice',serial='B',device='echo-b')

    def test_second_echo_does_not_change_the_first_streams_lease_or_intent(self):
        self.ns['_jam_send_echo']('room',self.a,self.state)
        intent=self.players['user:alice']['alexa_intent_at']
        self.players['user:alice'].update(playback_confirmed=True,playback_processing=False)
        self.ns['_jam_send_echo']('room',self.b,self.state)
        self.ns['_claim_alexa_output'].assert_called_once()
        self.assertEqual(len(self.updates),1)
        self.assertEqual(self.players['user:alice']['alexa_intent_at'],intent)
        self.assertTrue(self.players['user:alice']['playback_confirmed'])
        self.assertEqual([row[1] for row in self.sent],['A','B'])
        self.assertEqual(self.players['user:alice']['queue'],self.state['queue'])
        self.assertEqual(self.identity.get(),'owner')

    def test_newer_pause_prevents_an_old_queued_play_from_being_sent(self):
        old=dict(self.state)
        self.state=dict(self.state,playing=False,transport_revision=2)
        self.ns['_jam_send_echo']('room',self.a,old)
        self.assertEqual(self.sent,[])

    def test_account_context_is_isolated_for_cross_member_output_control(self):
        self.ns['_jam_send_echo']('room',self.a,self.state)
        self.ns['_jam_send_echo']('room',dict(self.b,identity='user:bob'),self.state)
        self.assertEqual([row[0] for row in self.sent],['user:alice','user:bob'])
        self.assertNotIn('owner',self.players)
        self.assertEqual(self.identity.get(),'owner')

    def test_natural_next_track_is_reported_and_adopted_without_a_restart(self):
        self.players['user:alice']=dict(self.state,queue_index=1,video_id='bbbbbbbbbbb',
            _jam_driver_room='room',_jam_driver_entry='first',_jam_driver_queue_version=1,
            playback_confirmed=True,playback_processing=False,position_ms=200)
        report=self.ns['_jam_echo_snapshot']('room',self.a,self.state)
        self.assertEqual(report['entry_id'],'second')
        self.state=dict(self.state,queue_index=1,video_id='bbbbbbbbbbb',entry_id='second',position_ms=200,duration_ms=20000)
        report=self.ns['_jam_echo_snapshot']('room',self.a,self.state)
        self.assertTrue(report['preserve'])
        self.assertEqual(self.players['user:alice']['_jam_driver_entry'],'second')
        self.assertEqual(self.sent,[])

    def test_queue_edits_update_upcoming_tracks_without_restarting_the_current_song(self):
        self.ns['_jam_send_echo']('room',self.a,self.state)
        self.players['user:alice'].update(playback_confirmed=True,playback_processing=False)
        changed=dict(self.state,queue_version=2,queue=self.state['queue'][:1])
        report=self.ns['_jam_echo_snapshot']('room',self.a,changed)
        self.assertTrue(report['confirmed']);self.assertEqual(len(self.sent),1)
        self.assertEqual(len(self.players['user:alice']['queue']),1)

    def test_volume_targets_only_the_selected_echo_and_is_not_sent_after_deselection(self):
        self.ns['_jam_send_echo']('room',self.b,dict(action='volume',value=75))
        self.ns['alexa_remote'].remote.command.assert_called_once_with('B','volume',75)
        self.ns['_record_volume_state'].assert_called_once_with('B',75,notify=True)
        self.state=dict(self.state,selected=['echo-a'])
        self.ns['_jam_send_echo']('room',self.b,dict(action='volume',value=10))
        self.assertEqual(self.ns['alexa_remote'].remote.command.call_count,1)

    def test_ending_host_jam_releases_echo_without_pause_or_late_restart(self):
        self.players['user:alice'] = dict(_jam_keep_playing_room='room', playing=True)
        self.ns['_jam_send_echo']('room', self.a, None)
        self.ns['_jam_send_echo']('room', self.a, self.state)
        self.ns['alexa_remote'].remote.command.assert_not_called()
        self.assertEqual(self.sent, [])
        self.assertTrue(self.players['user:alice']['playing'])

    def test_late_jam_cleanup_does_not_pause_a_new_personal_playback(self):
        self.players['user:alice']=dict(_jam_driver_room='another-room')
        self.ns['_jam_send_echo']('room',self.a,None)
        self.ns['alexa_remote'].remote.command.assert_not_called()

    def test_late_deselection_does_not_pause_an_output_that_was_reselected(self):
        self.players['user:alice']=dict(_jam_driver_room='room')
        self.ns['_jam_send_echo']('room',self.a,None)
        self.ns['alexa_remote'].remote.command.assert_not_called()
