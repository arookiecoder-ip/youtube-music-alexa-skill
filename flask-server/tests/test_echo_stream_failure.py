"""Echo URL normalization, stream routing and failure identity regressions."""
import ast
import asyncio
import copy
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from flask import Flask, request, has_request_context, jsonify
from datetime import datetime
import time, sys

SOURCE = Path(__file__).resolve().parents[1] / 'server.py'

class EchoStreamFailureTests(unittest.TestCase):
    def test_bare_production_hostname_becomes_https(self):
        tree = ast.parse(SOURCE.read_text())
        start = next(i for i,n in enumerate(tree.body) if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'PUBLIC_BASE_URL' for t in n.targets))
        env = {'os': SimpleNamespace(environ={'PUBLIC_BASE_URL': ' alexa.synthora.in/ '})}
        exec(compile(ast.Module(body=tree.body[start:start+2], type_ignores=[]), str(SOURCE), 'exec'), env)
        self.assertEqual(env['PUBLIC_BASE_URL'], 'https://alexa.synthora.in')

    def test_missing_config_uses_public_request_origin_not_ip_locked_google_url(self):
        tree = ast.parse(SOURCE.read_text())
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'Supporting')
        fn = next(n for n in cls.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'get_stream')
        fn.decorator_list = []
        direct = Mock(side_effect=AssertionError('Echo must never receive IP-locked URL'))
        ns = {'PUBLIC_BASE_URL': '', 'has_request_context': has_request_context, 'request': request,
              'threading': SimpleNamespace(Thread=Mock(return_value=Mock())), 'API_KEY': 'test-key',
              'Supporting': SimpleNamespace(ensure_downloaded=Mock(), resolve_direct_url=direct), 'asyncio': asyncio}
        exec(compile(ast.Module(body=[fn], type_ignores=[]), str(SOURCE), 'exec'), ns)
        with Flask(__name__).test_request_context('/', base_url='http://alexa.synthora.in'):
            result = asyncio.run(ns['get_stream']('abcdefghijk'))
        self.assertEqual(result['audio_url'], 'https://alexa.synthora.in/proxy/?video_id=abcdefghijk&key=test-key')
        direct.assert_not_called()

    def test_failed_old_stream_cannot_stop_new_selection(self):
        self._failure_case('oldtrack001', ignored=True)

    def test_failed_current_stream_stops_spinner_and_preserves_queue(self):
        self._failure_case('newtrack001', ignored=False)

    def _failure_case(self, video, ignored):
        tree = ast.parse(SOURCE.read_text())
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'alexa_state_event')
        fn.decorator_list = []
        state = {'video_id': 'newtrack001', 'playing': True, 'playback_processing': True,
                 'playback_revision': 3, 'queue': [{'video_id': 'newtrack001'}, {'video_id': 'nexttrack01'}]}
        original_queue = copy.deepcopy(state['queue'])
        notify = Mock()
        ns = {'request': request, 'jsonify': jsonify, 'datetime': datetime, 'time': time, 'sys': sys,
              '_np_lock': threading.RLock(), '_now_playing': state,
              '_playback_output': SimpleNamespace(snapshot=lambda: {'playback_output': 'alexa'}),
              '_valid_video_id': lambda x: isinstance(x, str) and len(x) == 11,
              '_computed_position_ms': lambda: 2000, '_reset_progress': Mock(), '_notify_sse': notify, 'logger': Mock()}
        exec(compile(ast.Module(body=[fn], type_ignores=[]), str(SOURCE), 'exec'), ns)
        with Flask(__name__).test_request_context('/', method='POST', json={
            'event': 'failed', 'video_id': video, 'error_type': 'MEDIA_ERROR_SERVICE_UNAVAILABLE'}):
            ns['alexa_state_event']()
        self.assertEqual(state['queue'], original_queue)
        if ignored:
            self.assertTrue(state['playing']); notify.assert_not_called()
        else:
            self.assertFalse(state['playing']); self.assertFalse(state['playback_processing'])
            self.assertEqual(state['playback_error']['type'], 'MEDIA_ERROR_SERVICE_UNAVAILABLE')
            notify.assert_called_once()

if __name__ == '__main__': unittest.main()
