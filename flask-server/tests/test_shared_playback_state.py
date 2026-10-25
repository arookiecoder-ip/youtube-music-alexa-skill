"""Multiple independent status readers must receive the same bounded error event."""
import ast
from pathlib import Path
import secrets
from types import SimpleNamespace
import unittest


class SharedPlaybackErrors(unittest.TestCase):
    def setUp(self):
        source = Path(__file__).resolve().parents[1] / 'server.py'
        node = next(node for node in ast.parse(source.read_text()).body
                    if isinstance(node, ast.FunctionDef) and node.name == '_np_snapshot')
        self.now = 100
        self.output = {'playback_output': 'alexa', 'output_token': 'lease-one'}
        self.state = dict(playing=False, title='Song', artist='Artist', thumbnail='', video_id='AAAAAAAAAAA',
                          playback_error={'type': 'timeout', 'message': 'Device did not respond'})
        self.namespace = dict(alexa_remote=SimpleNamespace(remote=SimpleNamespace(connection_state=True)), _now_playing=self.state, _volume_by_serial={}, secrets=secrets,
                              time=SimpleNamespace(time=lambda: self.now), _liked_version=0,
                              _playback_output=SimpleNamespace(snapshot=lambda: self.output),
                              _queue_version_locked=lambda: 1, _computed_position_ms=lambda: 0)
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), 'exec'), self.namespace)
        self.snapshot = self.namespace['_np_snapshot']

    def test_independent_web_readers_and_legacy_reader(self):
        first = self.snapshot(retain_errors=True)
        legacy = self.snapshot()
        second = self.snapshot(retain_errors=True)
        self.assertIsNotNone(first['playback_error'])
        self.assertEqual(first['playback_error_id'], second['playback_error_id'])
        self.assertEqual(first['playback_error'], legacy['playback_error'])
        self.assertIsNone(self.snapshot()['playback_error'])
        self.assertIsNotNone(self.snapshot(retain_errors=True)['playback_error'])

    def test_event_expires_and_does_not_leak_to_new_output_or_track(self):
        self.snapshot(retain_errors=True)
        self.now += 31
        self.assertIsNone(self.snapshot(retain_errors=True)['playback_error'])
        self.now -= 31
        self.output['output_token'] = 'lease-two'
        self.assertIsNone(self.snapshot(retain_errors=True)['playback_error'])
        self.output['output_token'] = 'lease-one'
        self.state['video_id'] = 'BBBBBBBBBBB'
        self.assertIsNone(self.snapshot(retain_errors=True)['playback_error'])

    def test_same_failure_again_is_a_new_event(self):
        first = self.snapshot(retain_errors=True)
        self.state['playback_error'] = dict(first['playback_error'])
        second = self.snapshot(retain_errors=True)
        self.assertNotEqual(first['playback_error_id'], second['playback_error_id'])
