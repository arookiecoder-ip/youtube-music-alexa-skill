"""Real SQLite dispatch fences and independent physical Jam output identities."""
import json
from unittest.mock import Mock
from test_jam_rooms import JamRoomsTests
from jam_echo import JamEchoDriver


class JamEchoTests(JamRoomsTests):
    def register(self, agent='host-phone', member=None, identity='user:host', active=True):
        self.rooms.presence('room',agent,member,'Host device',details=dict(identity=identity,username='Host',kind='alexa' if active else 'phone',devices=[
            dict(id='phone',kind='phone',name='Phone',active=not active,online=True),
            dict(id='alexa:serial',kind='alexa',name='Living room Echo',active=active,online=True)]))
        return next(o for o in self.rooms.outputs('room') if json.loads(o['details']).get('server_echo'))

    def test_echo_is_selectable_only_in_single_device_mode(self):
        echo=self.register()
        self.assertEqual(self.state()['selected'],[echo['device']])
        with self.assertRaisesRegex(ValueError,'single-device'):
            self.command('configure',mode='multiple',selected=['host-phone',echo['device']])
        self.assertEqual(self.command('configure',mode='single',selected=[echo['device']])['selected'],[echo['device']])
        self.assertEqual(self.command('configure',mode='multiple',selected=['host-phone'])['selected'],['host-phone'])
        root=next(o for o in self.rooms.outputs('room') if o['device']=='host-phone')
        self.assertEqual([d['kind'] for d in json.loads(root['details'])['devices']],['phone'])

    def test_duplicate_apps_do_not_duplicate_the_same_account_echo(self):
        self.register()
        self.register('tablet',active=False)
        outputs=self.rooms.outputs('room')
        self.assertEqual(len([o for o in outputs if json.loads(o['details']).get('server_echo')]),1)
        self.assertEqual(len(outputs),3)

    def test_starting_jam_adopts_an_already_playing_echo_without_restarting_it(self):
        echo=self.register();state=self.state();send=Mock()
        snapshot=Mock(return_value=dict(video_id=state['video_id'],playing=True,confirmed=True,processing=False,position_ms=state['position_ms'],seek_revision=0))
        driver=JamEchoDriver(self.rooms,send,snapshot)
        driver._dispatch('room',echo,state)
        send.assert_not_called()
        self.assertTrue(self.rooms.presented('room',self.state())['playback_confirmed'])

    def test_new_song_is_dispatched_once_and_stale_confirmation_is_fenced(self):
        echo=self.register();send=Mock(return_value=None);driver=JamEchoDriver(self.rooms,send,Mock(return_value=None))
        state=self.state();driver._dispatch('room',echo,state);driver._dispatch('room',echo,state)
        self.assertEqual(send.call_count,1)
        old_intent=None
        with self.database() as db:old_intent=db.execute('SELECT intent FROM jam_echo_intents').fetchone()['intent']
        next_state=self.command('next');driver._dispatch('room',echo,next_state)
        self.assertEqual(send.call_count,2)
        self.assertFalse(self.rooms.echo_report('room',echo['device'],old_intent,dict(confirmed=True,playing=True)))

    def test_echo_failure_backoff_is_not_reset_by_inventory_heartbeats(self):
        echo=self.register();send=Mock(return_value='Amazon temporarily busy')
        driver=JamEchoDriver(self.rooms,send,Mock(return_value=None));state=self.state()
        driver._dispatch('room',echo,state)
        for _ in range(5):
            self.now+=2;echo=self.register();driver._dispatch('room',echo,state)
        self.assertEqual(send.call_count,1)
        self.now+=6;echo=self.register();driver._dispatch('room',echo,state)
        self.assertEqual(send.call_count,2)

    def test_one_app_leaving_does_not_disconnect_another_apps_shared_echo(self):
        first=self.members.join('room','alice','Alice','alice-phone')
        second=self.members.join('room','alice','Alice','alice-tablet')
        self.register('alice-tablet',second,'user:alice',False)
        echo=self.register('alice-phone',first,'user:alice',False)
        self.command('configure',mode='single',selected=[echo['device']])
        self.members.leave(first)
        self.assertNotIn('alice-phone',self.state()['selected'])
        self.assertIn(echo['device'],self.state()['selected'])
        self.assertTrue(any(o['device']==echo['device'] for o in self.rooms.outputs('room')))
        self.members.leave(second)
        self.assertNotIn(echo['device'],self.state()['selected'])

    def test_the_same_physical_echo_cannot_be_reserved_by_two_live_rooms(self):
        echo=self.register()
        with self.database() as db:
            db.execute("INSERT INTO jam_tokens VALUES('other',1000,'other-host')");db.commit()
        self.rooms.start('other',{'queue':self.songs(),'queue_index':0,'playing':True},'other-phone')
        self.rooms.presence('other','other-phone',None,'Other phone',details=dict(identity='user:host',username='Host',kind='phone',devices=[dict(id='alexa:serial',kind='alexa',name='Echo',active=False)]))
        state=self.rooms.transact('other')
        body=dict(command_id='reserve',revision=state['revision'],mode='single',selected=[echo['device']])
        from jam_rooms import JamConflict
        with self.assertRaises(JamConflict):self.rooms.transact('other','configure',body)
        with self.database() as db:db.execute("DELETE FROM jam_tokens WHERE token='room'");db.commit()
        self.assertEqual(self.rooms.transact('other','configure',body)['selected'],[echo['device']])

    def test_unconfirmed_startup_has_a_deadline_instead_of_an_endless_spinner(self):
        echo=self.register();send=Mock(return_value=None)
        driver=JamEchoDriver(self.rooms,send,Mock(return_value=None),clock=lambda:self.now)
        state=self.state();driver._dispatch('room',echo,state)
        self.now+=16;echo=next(o for o in self.rooms.outputs('room') if o['device']==echo['device'])
        driver._dispatch('room',echo,state)
        failed=next(o for o in self.rooms.outputs('room') if o['device']==echo['device'])
        self.assertIn('did not confirm',failed['error'])
        self.assertFalse(json.loads(failed['status'])['processing'])
        self.assertEqual(send.call_count,1)

    def test_volume_failure_does_not_turn_working_playback_into_buffering(self):
        echo=self.register();self.rooms.claim_echo('room',echo['device'],'playing')
        self.rooms.echo_report('room',echo['device'],'playing',dict(video_id='aaaaaaaaaaa',playing=True,confirmed=True,processing=False,position_ms=1000,volume=25))
        self.rooms.volume('room',echo['device'],80)
        command=self.rooms.device_commands('room',echo['device'],None)[0]
        self.rooms.echo_volume_failed('room',echo['device'],command['id'],'Amazon temporarily busy')
        self.assertTrue(self.rooms.presented('room',self.state())['playback_confirmed'])
        output=next(o for o in self.rooms.outputs('room') if o['device']==echo['device'])
        self.assertEqual(json.loads(output['status'])['volume'],25)
        self.assertIn('Volume:',json.loads(output['status'])['volume_error'])

    def test_repeat_restart_clears_the_old_finished_report_before_dispatch(self):
        echo=self.register();self.command('repeat');self.command('repeat')
        state=self.state();self.rooms.claim_echo('room',echo['device'],'finished')
        self.rooms.echo_report('room',echo['device'],'finished',dict(video_id=state['video_id'],entry_id=state['entry_id'],
            ended=True,playing=False,confirmed=True,processing=False,position_ms=10000,seek_revision=0))
        repeated=self.state();self.assertEqual(repeated['seek_revision'],1)
        sent=[]
        def send(token,details,desired):
            self.assertEqual(self.state()['seek_revision'],desired['seek_revision'])
            sent.append(desired['seek_revision'])
        driver=JamEchoDriver(self.rooms,send,Mock(return_value=None),clock=lambda:self.now)
        driver._dispatch('room',echo,repeated)
        self.assertEqual(sent,[1]);self.assertEqual(self.state()['seek_revision'],1)

    def test_existing_multiple_output_room_deselects_echo_on_refresh(self):
        echo=self.register()
        with self.database() as db:
            row=db.execute("SELECT state FROM jam_rooms WHERE token='room'").fetchone()
            state=json.loads(row['state']);state.update(mode='multiple',selected=['host-phone',echo['device']])
            db.execute("UPDATE jam_rooms SET state=? WHERE token='room'",(json.dumps(state),));db.commit()
        self.assertEqual(self.state()['selected'],['host-phone'])
