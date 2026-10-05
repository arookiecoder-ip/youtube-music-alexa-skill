"""Exercise the actual queue route without starting Alexa or downloading audio."""
import ast
import copy
from pathlib import Path
import re
import threading
import time
import unittest
import sys
from unittest.mock import Mock
from flask import Flask, jsonify, request
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from playback_output import PlaybackOutput, OutputConflict

A, B, C, D = 'aaaaaaaaaaa', 'bbbbbbbbbbb', 'ccccccccccc', 'ddddddddddd'

def track(video, title=None):
    return {'video_id': video, 'title': title or video, 'artist': 'Artist', 'duration_ms': 180000, 'thumbnail': 'image'}

class PhoneQueueTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.state = {'queue': [track(A), track(B)], 'queue_index': 0, 'video_id': A,
                      'playing': True, 'position_ms': 7000, 'playback_revision': 0}
        self.notify = Mock()
        self.echo = Mock()
        self.output = PlaybackOutput()
        namespace = {'app': self.app, 'request': request, 'jsonify': jsonify, 'time': time,
            'error_response': lambda message, status: (jsonify({'error': message}), status),
            '_np_lock': threading.RLock(), '_now_playing': self.state, '_notify_sse': self.notify,
            '_thumbnail_url': lambda raw: raw.get('url', '') if isinstance(raw, dict) else (raw or ''),
            '_valid_video_id': lambda value: isinstance(value, str) and bool(re.fullmatch(r'[A-Za-z0-9_-]{11}', value)),
            '_reset_progress': lambda position: self.state.update(position_ms=position, started_at=time.time()),
            '_computed_position_ms': lambda: self.state['position_ms'], 'alexa_remote': self.echo,
            '_playback_output': self.output, 'OutputConflict': OutputConflict}
        source = Path(__file__).resolve().parents[1] / 'server.py'
        routes = [node for node in ast.parse(source.read_text()).body
                  if isinstance(node, ast.FunctionDef) and node.name in ('app_queue', 'handle_output_conflict')]
        exec(compile(ast.Module(body=routes, type_ignores=[]), str(source), 'exec'), namespace)
        self.namespace = namespace
        self.client = self.app.test_client()

    def post(self, action, after=A, tracks=None, **extra):
        return self.client.post('/api/app/queue/', json={'action': action, 'after': after,
            'tracks': [] if tracks is None else tracks, **extra})

    def claim(self):
        return self.output.phone('phone-one', lambda: None)['output_token']

    def test_delayed_phone_updates_cannot_overwrite_alexa_takeover(self):
        token = self.claim()
        self.output.alexa(wait=False)
        before = copy.deepcopy(self.state)
        for action, items in [('start', [track(C)]), ('current', []), ('extend', [track(C)])]:
            result = self.post(action, after=C if action == 'start' else A, tracks=items,
                               output_owner='phone-one', output_token=token)
            self.assertEqual(result.status_code, 409)
            self.assertEqual(result.json['error']['code'], 'output_changed')
        self.assertEqual(self.state, before)
        self.notify.assert_not_called()

    def test_phone_local_order_wins_without_dispatching_an_echo(self):
        token = self.claim()
        result = self.post('start', after=C, tracks=[track(C), track(A), track(B)], queue_index=0,
                           output_owner='phone-one', output_token=token, playing=True)
        self.assertEqual(result.status_code, 200)
        self.assertEqual([row['video_id'] for row in self.state['queue']], [C, A, B])
        self.assertTrue(self.state['playback_confirmed'])
        self.assertFalse(self.state['playback_processing'])
        self.assertEqual(self.echo.mock_calls, [])

    def test_phone_pause_is_settled_but_real_buffering_is_not(self):
        token = self.claim()
        extra = dict(output_owner='phone-one', output_token=token)
        self.assertEqual(self.post('current', playing=False, buffering=False, **extra).status_code, 200)
        self.assertTrue(self.state['playback_confirmed'])
        self.assertFalse(self.state['playback_processing'])
        self.assertEqual(self.post('current', playing=False, buffering=True, **extra).status_code, 200)
        self.assertFalse(self.state['playback_confirmed'])
        self.assertTrue(self.state['playback_processing'])

    def test_canonical_queue_url_updates_without_redirect(self):
        response = self.client.post('/api/app/queue', json={
            'action': 'current', 'after': A, 'tracks': [], 'queue_index': 0,
            'playing': True, 'position_ms': 9000})
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.headers.get('Location'))
        self.assertEqual(self.state['position_ms'], 9000)
        self.assertEqual(self.echo.mock_calls, [])

    def test_full_collection_start_is_atomic_and_preserves_duplicate_cursor(self):
        tracks = [track(A)] * 5000
        response = self.post('start', tracks=tracks, queue_index=4999, playing=True, position_ms=12000)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(self.state['queue']), 5000)
        self.assertEqual(self.state['queue_index'], 4999)
        self.assertEqual(self.state['position_ms'], 12000)
        self.assertEqual(self.echo.mock_calls, [])

    def test_full_start_and_incremental_edits_keep_distinct_limits(self):
        before = copy.deepcopy(self.state)
        self.assertEqual(self.post('start', tracks=[track(A)] * 5001).status_code, 400)
        self.assertEqual(self.post('extend', tracks=[track(C)] * 201).status_code, 400)
        self.assertEqual(self.state, before)

    def test_start_publishes_queue_and_selected_track_without_echo_command(self):
        result = self.post('start', after=B, tracks=[track(A), track(B, 'Selected')])
        self.assertEqual(result.status_code, 200)
        self.assertEqual((self.state['video_id'], self.state['title'], self.state['queue_index']), (B, 'Selected', 1))
        self.assertEqual(self.state['position_ms'], 0)
        self.assertFalse(self.state['playback_confirmed'])
        self.echo.assert_not_called()
        self.assertEqual(self.echo.mock_calls, [])
        self.notify.assert_called_once()

    def test_next_inserts_after_current(self):
        self.assertEqual(self.post('next', tracks=[track(C)]).status_code, 200)
        self.assertEqual([t['video_id'] for t in self.state['queue']], [A, C, B])

    def test_extend_appends_and_deduplicates_radio(self):
        self.assertEqual(self.post('extend', tracks=[track(B), track(C), track(C)]).status_code, 200)
        self.assertEqual([t['video_id'] for t in self.state['queue']], [A, B, C])

    def test_current_advances_metadata_and_progress_without_replacing_queue(self):
        queue = self.state['queue']
        self.assertEqual(self.post('current', after=B, playing=True, position_ms=23000).status_code, 200)
        self.assertIs(self.state['queue'], queue)
        self.assertEqual(self.state['video_id'], B)
        self.assertEqual(self.state['queue_index'], 1)
        self.assertEqual(self.state['position_ms'], 23000)
        self.assertTrue(self.state['playback_confirmed'])

    def test_current_moves_to_exact_duplicate_occurrence_without_reordering(self):
        self.state['queue'] = [track(A), track(B), track(A, 'Repeated song'), track(C)]
        self.state.update(video_id=B, queue_index=1)
        queue = self.state['queue']
        self.assertEqual(self.post('current', after=A, queue_index=2, playing=True, position_ms=23000).status_code, 200)
        self.assertIs(self.state['queue'], queue)
        self.assertEqual((self.state['queue_index'], self.state['title']), (2, 'Repeated song'))
        self.assertEqual(self.state['position_ms'], 23000)
        self.assertEqual(self.echo.mock_calls, [])

    def test_same_video_new_occurrence_resets_progress(self):
        self.state['queue'] = [track(A), track(B), track(A)]
        self.assertEqual(self.post('current', after=A, queue_index=2).status_code, 200)
        self.assertEqual(self.state['position_ms'], 0)
        self.assertEqual(self.state['queue_index'], 2)

    def test_stale_duplicate_cursor_does_not_change_playback(self):
        before = copy.deepcopy(self.state)
        for index in (1, 200):
            self.assertEqual(self.post('current', after=A, queue_index=index, playing=True).status_code, 409)
        self.assertEqual(self.state, before)
        self.notify.assert_not_called()

    def test_invalid_cursor_is_rejected_before_state_changes(self):
        before = copy.deepcopy(self.state)
        for index in (-1, True, '0', 0.5):
            self.assertEqual(self.post('current', queue_index=index).status_code, 400)
        self.assertEqual(self.state, before)

    def test_current_pause_keeps_position(self):
        self.assertEqual(self.post('current', playing=False, position_ms=18000).status_code, 200)
        self.assertEqual(self.state['position_ms'], 18000)
        self.assertFalse(self.state['playing'])

    def test_stale_updates_do_not_resurrect_removed_tracks(self):
        before = copy.deepcopy(self.state)
        for action in ('current', 'next', 'extend'):
            self.assertEqual(self.post(action, after=D, tracks=[] if action == 'current' else [track(C)]).status_code, 409)
        self.assertEqual(self.state, before)
        self.notify.assert_not_called()

    def test_invalid_payloads_leave_queue_unchanged(self):
        before = copy.deepcopy(self.state)
        for payload in [None, [], {'action': 'invalid'}, {'action': 'start', 'after': A, 'tracks': []},
                        {'action': 'current', 'after': A, 'tracks': [track(A)]},
                        {'action': 'current', 'after': A, 'playing': 'true'},
                        {'action': 'current', 'after': A, 'position_ms': -1},
                        {'action': 'start', 'after': A, 'tracks': [track('invalid')]}]:
            self.assertEqual(self.client.post('/api/app/queue/', json=payload).status_code, 400)
        self.assertEqual(self.state, before)

    def test_next_for_a_noncurrent_song_is_rejected(self):
        self.assertEqual(self.post('next', after=B, tracks=[track(C)]).status_code, 409)

if __name__ == '__main__':
    unittest.main()
