"""Voice playback remains observable after Amazon remote auth expires."""
import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import unittest
from flask import Flask,request,jsonify

class LoggedOutEchoTests(unittest.TestCase):
    def setUp(self):
        self.out={'playback_output':'alexa','output_serial':'echo-one','output_token':'echo-token'}
        self.np={'playing':True,'playback_processing':False,'video_id':'AAAAAAAAAAA'}
        self.remote=SimpleNamespace(connection_state=False,_devices_raw=[{'serialNumber':'echo-one','accountName':'Bedroom'}],devices=Mock(return_value=(None,'Not signed in')),command=Mock())
        self.app=Flask(__name__);self.ns=dict(app=self.app,request=request,jsonify=jsonify,
            _playback_output=SimpleNamespace(snapshot=lambda:dict(self.out)),alexa_remote=SimpleNamespace(remote=self.remote),
            _get_now_playing=lambda:dict(self.np),error_response=lambda message,status,**kwargs:(jsonify(error={'message':message,**kwargs}),status),
            _cancel_pending_dispatch=Mock(),OutputConflict=RuntimeError)
        source=Path(__file__).resolve().parents[1]/'server.py'
        names={'guard_logged_out_echo_controls','_active_logged_out_echo','alexa_devices','_pause_echo_for_mobile'}
        nodes=[n for n in ast.parse(source.read_text()).body if isinstance(n,ast.FunctionDef) and n.name in names]
        exec(compile(ast.Module(body=nodes,type_ignores=[]),str(source),'exec'),self.ns)
        self.mutations=Mock(return_value='ok')
        for path in ['/alexa/command/','/alexa/seek/','/alexa/queue_remove/','/alexa/state_event/','/api/app/queue/']:
            self.app.add_url_rule(path,path,lambda:self.mutations(),methods=['POST'])
        self.client=self.app.test_client()
    def test_only_active_echo_is_retained_as_logged_out(self):
        devices=self.client.get('/alexa/devices/').json['devices']
        self.assertEqual(devices,[{'serial':'echo-one','name':'Bedroom','online':True,'logged_out':True,'controllable':False}])
        self.np['playing']=False
        self.assertEqual(self.client.get('/alexa/devices/').json['devices'],[])
    def test_voice_paused_current_track_remains_visible_until_handoff(self):
        self.np.update(playing=False,playback_confirmed=True)
        self.assertEqual(len(self.client.get('/alexa/devices/').json['devices']),1)
        self.np['_alexa_finished_token']='finished'
        self.assertEqual(self.client.get('/alexa/devices/').json['devices'],[])
    def test_handoff_removes_logged_out_echo(self):
        self.out['playback_output']='phone'
        self.assertEqual(self.client.get('/alexa/devices/').json['devices'],[])
    def test_controls_fail_before_any_queue_or_transport_mutation(self):
        for path in ['/alexa/command/','/alexa/seek/','/alexa/queue_remove/','/api/app/queue/']:
            response=self.client.post(path,json={})
            self.assertEqual(response.status_code,409)
            self.assertEqual(response.json['error']['code'],'amazon_login_required')
        self.mutations.assert_not_called()
    def test_voice_events_and_phone_controls_remain_available(self):
        self.assertEqual(self.client.post('/alexa/state_event/',json={}).status_code,200)
        self.out['playback_output']='phone'
        self.assertEqual(self.client.post('/alexa/command/',json={'serial':'mobile:phone'}).status_code,200)
    def test_signed_out_handoff_does_not_require_unavailable_amazon_pause(self):
        import threading
        self.ns.update(_np_lock=threading.Lock(),_now_playing=self.np,_reset_progress=Mock(),_computed_position_ms=lambda:12000)
        self.ns['_pause_echo_for_mobile']('echo-one')
        self.remote.command.assert_not_called()
        self.ns['_cancel_pending_dispatch'].assert_called_once_with('echo-one')
        self.assertFalse(self.np['playing'])
    def test_signed_in_echo_restores_devices_and_commands(self):
        self.remote.connection_state=True;self.remote.devices.return_value=([{'serial':'echo-one','name':'Bedroom','online':True}],None)
        self.assertTrue(self.client.get('/alexa/devices/').json['amazon_connected'])
        self.assertEqual(self.client.post('/alexa/command/',json={}).status_code,200)
