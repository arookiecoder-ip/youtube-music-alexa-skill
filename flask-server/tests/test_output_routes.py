"""Real Flask ownership/queue handlers with only physical Echo calls stubbed."""
import ast
from pathlib import Path
import threading
from types import SimpleNamespace
from unittest.mock import Mock, AsyncMock
import asyncio
import copy

from test_app_queue import PhoneQueueTests, A, B, track


class OutputRoutesTests(PhoneQueueTests):
    def setUp(self):
        super().setUp()
        self.seek_dispatch = Mock(return_value=None)
        self.namespace['_dispatch_play_with_retry'] = self.seek_dispatch
        self.arm = Mock()
        self.namespace.update(copy=copy, threading=threading, logger=Mock(), _effective_serial=lambda serial: serial,
            _touch_cached_audio=Mock(), _ensure_audio_ready_for_play=Mock(), _arm_play=self.arm,
            _watch_resume_confirmation=Mock(), _ARMED_PLAYS_LOCK=threading.Lock(), _ARMED_PLAYS={})
        self.command = Mock(side_effect=lambda *args: self.state.update(playing=False, playback_processing=False))
        self.namespace.update(alexa_remote=SimpleNamespace(remote=SimpleNamespace(command=self.command)),
                              _get_now_playing=lambda: dict(self.state),
                              _cancel_pending_dispatch=Mock(), _bump_playback_generation=Mock())
        source = Path(__file__).resolve().parents[1] / 'server.py'
        functions = [node for node in ast.parse(source.read_text()).body
                     if isinstance(node, ast.FunctionDef) and node.name in
                     ('app_playback_output', '_claim_alexa_output', 'alexa_command', 'alexa_seek')]
        exec(compile(ast.Module(body=functions, type_ignores=[]), str(source), 'exec'), self.namespace)

    def output_request(self, action, token=''):
        return self.client.post('/api/app/output/', json={'action': action,
            'output_owner': 'phone-one', 'output_token': token, 'serial': 'echo-one'})

    def test_claim_pauses_echo_and_phone_publish_does_not_start_it(self):
        claim = self.output_request('claim')
        self.assertEqual(claim.status_code, 200)
        self.command.assert_called_once_with('echo-one', 'pause')
        result = self.post('start', tracks=[track(A), track(B)], playing=True,
                           output_owner='phone-one', output_token=claim.json['output_token'])
        self.assertEqual(result.status_code, 200)
        self.assertTrue(self.state['playing'])
        self.assertTrue(self.state['playback_confirmed'])
        self.assertFalse(self.state['playback_processing'])
        self.command.assert_called_once_with('echo-one', 'pause')
        self.assertEqual(self.client.get('/api/app/output/').json['playback_output'], 'phone')

    def test_echo_pause_failure_preserves_alexa_state(self):
        self.command.side_effect = None
        self.command.return_value = 'offline'
        result = self.output_request('claim')
        self.assertEqual(result.status_code, 409)
        self.assertEqual(result.json['playback_output'], 'alexa')
        self.assertTrue(self.state['playing'])

    def test_web_handoff_waits_for_ack_and_invalidates_phone_writes(self):
        claim = self.output_request('claim').json
        changed = threading.Event()
        self.namespace['_notify_sse'] = changed.set
        dispatched = threading.Event()
        errors = []
        def play_on_web():
            try:
                self.namespace['_claim_alexa_output']('echo-one')
                dispatched.set()
            except Exception as error:
                errors.append(error)
        worker = threading.Thread(target=play_on_web)
        worker.start()
        self.assertTrue(changed.wait(1))
        self.assertFalse(dispatched.is_set())
        output = self.client.get('/api/app/output/').json
        stale = self.post('current', playing=True, output_owner='phone-one',
                          output_token=claim['output_token'])
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(self.output_request('ack', output['output_token']).status_code, 200)
        worker.join(1)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertTrue(dispatched.is_set())
        # The handoff itself is settled; only an actual play dispatch starts buffering.
        self.assertFalse(self.state['playback_processing'])

    def test_phone_radio_lookup_does_not_replace_queue_or_claim_echo(self):
        source = Path(__file__).resolve().parents[1] / 'server.py'
        function = next(node for node in ast.parse(source.read_text()).body
                        if isinstance(node, ast.AsyncFunctionDef) and node.name == 'get_radio')
        # Call the real handler in a request context; no Flask async adapter needed.
        function.decorator_list = []
        self.namespace.update(Supporting=SimpleNamespace(get_radio_queue=AsyncMock(return_value=[track(B)])),
                              logger=Mock(), _update_now_playing=Mock())
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(source), 'exec'), self.namespace)
        claim = self.output_request('claim').json
        before = copy.deepcopy(self.state)
        with self.app.test_request_context('/get_radio/?video_id=' + A + '&update_queue=0'):
            response = asyncio.run(self.namespace['get_radio']())
        self.assertEqual(response.json['playlist'], [track(B)])
        self.assertEqual(self.state, before)
        self.assertTrue(self.output.owns_phone('phone-one', claim['output_token']))
        self.namespace['_update_now_playing'].assert_not_called()
        with self.app.test_request_context('/get_radio/?video_id=' + A):
            response = asyncio.run(self.namespace['get_radio']())
        self.assertEqual(response.json['playlist'], [track(B)])
        self.assertEqual(self.state, before)
        self.assertTrue(self.output.owns_phone('phone-one', claim['output_token']))

    def test_phone_cannot_start_before_actual_echo_stop_confirmation(self):
        accepted = threading.Event()
        finished = threading.Event()
        responses = []
        self.command.side_effect = lambda *args: accepted.set() and None
        def claim_phone():
            with self.app.test_client() as client:
                responses.append(client.post('/api/app/output/', json={
                    'action': 'claim', 'output_owner': 'phone-one', 'serial': 'echo-one'}))
            finished.set()
        worker = threading.Thread(target=claim_phone)
        worker.start()
        self.assertTrue(accepted.wait(1))
        self.assertFalse(finished.is_set())
        self.assertEqual(self.output.snapshot()['playback_output'], 'alexa')
        self.state.update(playing=False, playback_processing=False)
        worker.join(1)
        self.assertFalse(worker.is_alive())
        self.assertEqual(responses[0].status_code, 200)
        self.assertEqual(responses[0].json['playback_output'], 'phone')

    def run_real_web_play(self, error=None):
        self.output_request('claim')
        changed = threading.Event()
        self.namespace['_notify_sse'] = changed.set
        self.command.side_effect = None
        self.command.return_value = error
        responses = []
        def play():
            with self.app.test_client() as client:
                responses.append(client.post('/alexa/command/', json={'serial': 'echo-one', 'action': 'play'}))
        worker = threading.Thread(target=play)
        worker.start()
        self.assertTrue(changed.wait(1))
        self.command.assert_called_once_with('echo-one', 'pause')
        output = self.client.get('/api/app/output/').json
        self.assertEqual(self.output_request('ack', output['output_token']).status_code, 200)
        worker.join(1)
        self.assertFalse(worker.is_alive())
        return responses[0]

    def test_real_web_play_arms_phone_song_and_offset_and_keeps_true_loading(self):
        result = self.run_real_web_play()
        self.assertEqual(result.status_code, 200)
        self.arm.assert_called_once_with('echo-one', A, 7000, kind='resume')
        self.assertTrue(self.state['playing'])
        self.assertFalse(self.state['playback_confirmed'])
        self.assertTrue(self.state['playback_processing'])
        self.command.assert_called_with('echo-one', 'play', None)

    def test_failed_web_play_does_not_leave_phone_mirror_loading_forever(self):
        result = self.run_real_web_play('offline')
        self.assertEqual(result.status_code, 502)
        self.assertFalse(self.state['playing'])
        self.assertTrue(self.state['playback_confirmed'])
        self.assertFalse(self.state['playback_processing'])

    def test_seek_after_phone_pause_only_moves_cursor_and_never_starts_alexa(self):
        self.output_request('claim')
        changed = threading.Event()
        self.namespace['_notify_sse'] = changed.set
        responses = []
        def seek():
            with self.app.test_client() as client:
                responses.append(client.post('/alexa/seek/', json={'serial': 'echo-one', 'position_ms': 42000}))
        worker = threading.Thread(target=seek)
        worker.start()
        self.assertTrue(changed.wait(1))
        output = self.client.get('/api/app/output/').json
        self.assertEqual(self.output_request('ack', output['output_token']).status_code, 200)
        worker.join(1)
        self.assertFalse(worker.is_alive())
        self.assertEqual(responses[0].status_code, 200)
        self.assertTrue(responses[0].json['paused'])
        self.assertFalse(self.state['playing'])
        self.assertEqual(self.state['position_ms'], 42000)
        self.seek_dispatch.assert_not_called()

    def test_superseded_app_resume_cannot_dispatch_or_replace_latest_phone_state(self):
        old = self.output_request('claim').json
        latest = self.output_request('claim').json
        before = copy.deepcopy(self.state)
        result = self.client.post('/alexa/command/', json={'serial': 'echo-one', 'action': 'play',
            'output_owner': 'phone-one', 'output_token': old['output_token']})
        self.assertEqual(result.status_code, 409)
        self.assertEqual(self.state, before)
        self.assertTrue(self.output.owns_phone('phone-one', latest['output_token']))
        self.command.assert_called_once_with('echo-one', 'pause')

    def test_echo_cleanup_does_not_pause_or_mark_the_phone_as_loading(self):
        claim = self.output_request('claim').json
        self.post('current', playing=True, output_owner='phone-one', output_token=claim['output_token'])
        before = copy.deepcopy(self.state)
        self.command.side_effect = None
        self.command.return_value = None
        result = self.client.post('/alexa/command/', json={'serial': 'echo-one', 'action': 'pause'})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(self.state, before)
        self.assertTrue(self.state['playing'])
        self.assertFalse(self.state['playback_processing'])
