import ast
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock
from flask import Flask, jsonify, request
import contextvars

from playback_timers import PlaybackTimers, same_output


class PlaybackTimersTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = str(Path(self.temp.name) / 'timer.db')
        self.now = 1000
        @contextmanager
        def database():
            db = sqlite3.connect(self.path, timeout=10)
            db.row_factory = sqlite3.Row
            try:
                yield db
            finally:
                db.close()
        self.database = database
        self.timers = PlaybackTimers(database, lambda: self.now)
        self.target = dict(playback_output='alexa', output_serial='echo', output_owner='', handoff_pending=False)

    def test_deadline_replacement_cancellation_and_identity_isolation(self):
        first = self.timers.set('alice', 1, self.target)
        self.timers.set('bob', 2, self.target)
        replacement = self.timers.set('alice', 3, self.target)
        self.timers.cancel('alice', first['id'])
        self.assertEqual(self.timers.get('alice')['id'], replacement['id'])
        self.timers.cancel('alice')
        self.assertIsNone(self.timers.get('alice'))
        self.assertIsNotNone(self.timers.get('bob'))
        for value in (True, 0, -1, 181, '5', None, 1.5):
            with self.assertRaises(ValueError):
                self.timers.set('alice', value, self.target)

    def test_restart_and_worker_leases_retry_unconfirmed_pause(self):
        self.timers.set('alice', 1, self.target)
        self.now += 60
        restarted = PlaybackTimers(self.database, lambda: self.now)
        with ThreadPoolExecutor(max_workers=4) as workers:
            results = list(workers.map(lambda _: restarted.due(), range(4)))
        self.assertEqual(sum(map(len, results)), 1)
        self.now += 30
        self.assertEqual(len(restarted.due()), 1)

    def test_handoff_new_output_and_other_phone_are_not_paused(self):
        self.assertTrue(same_output(self.target, self.target))
        self.assertTrue(same_output(self.target, dict(self.target, output_serial='')))
        self.assertFalse(same_output(self.target, dict(self.target, output_serial='other')))
        self.assertFalse(same_output(self.target, dict(self.target, handoff_pending=True)))
        phone = dict(playback_output='phone', output_owner='alice-phone')
        self.assertTrue(same_output(phone, phone))
        self.assertFalse(same_output(phone, dict(phone, output_owner='other-phone')))
        self.assertFalse(same_output(self.target, phone))

    def route_namespace(self):
        app = Flask(__name__)
        app.testing = True
        identity = contextvars.ContextVar('identity', default='alice')
        ns = dict(json=json, request=request, jsonify=jsonify, _current_identity=identity.get, _PLAYER_IDENTITY=identity,
                  _playback_timers=self.timers, _playback_output=Mock(), _jam_access_token=lambda: (None, None),
                  error_response=lambda message, status: (jsonify(error=message), status),
                  app=app, _sleep_same_output=same_output, time=Mock(),
                  _mobile_devices=Mock(), _get_now_playing=lambda: {}, alexa_command=Mock(return_value=({}, 200)),
                  _jam_token_valid=Mock(return_value=True), _jam_members=Mock(), _jam_rooms=Mock(), _jam_drive=Mock())
        ns['_playback_output'].snapshot.return_value = self.target
        ns['time'].time.return_value = self.now
        tree = ast.parse((Path(__file__).resolve().parents[1] / 'server.py').read_text())
        nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in ('playback_sleep_timer', '_expire_playback_timer')]
        for node in nodes:
            node.decorator_list = []
        exec(compile(ast.Module(body=nodes, type_ignores=[]), 'server.py', 'exec'), ns)
        app.add_url_rule('/timer', view_func=ns['playback_sleep_timer'], methods=['GET', 'POST', 'DELETE'])
        return ns

    def test_route_validation_and_remote_deadline(self):
        ns = self.route_namespace()
        client = ns['app'].test_client()
        self.assertEqual(client.post('/timer', json={'minutes': True}).status_code, 400)
        response = client.post('/timer', json={'minutes': 5})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['deadline_ms'], 1300000)
        self.assertEqual(client.post('/timer', json={'minutes': 0}).json['deadline_ms'], 0)
        self.assertEqual(client.get('/timer').json['deadline_ms'], 0)

    def test_expiry_is_pause_not_toggle_and_cannot_pause_replacement_output(self):
        ns = self.route_namespace()
        self.timers.set('alice', 1, self.target)
        self.now += 60
        ns['time'].time.return_value = self.now
        timer = self.timers.due()[0]
        ns['_expire_playback_timer'](timer)
        ns['alexa_command'].assert_called_once()
        self.assertIsNone(self.timers.get('alice'))
        self.timers.set('alice', 1, self.target)
        self.now += 60
        ns['_playback_output'].snapshot.return_value = dict(self.target, output_serial='other')
        ns['_expire_playback_timer'](self.timers.due()[0])
        self.assertEqual(ns['alexa_command'].call_count, 1)

    def test_failed_remote_pause_retries_without_losing_timer(self):
        ns = self.route_namespace()
        ns['alexa_command'].return_value = ({}, 502)
        self.timers.set('alice', 1, self.target)
        self.now += 60
        with self.assertRaises(RuntimeError):
            ns['_expire_playback_timer'](self.timers.due()[0])
        self.assertIsNotNone(self.timers.get('alice'))
        self.assertEqual(self.timers.due(), [])
        self.now += 30
        ns['alexa_command'].return_value = ({}, 200)
        ns['_expire_playback_timer'](self.timers.due()[0])
        self.assertIsNone(self.timers.get('alice'))

    def test_removed_jam_member_cannot_pause_the_room(self):
        ns = self.route_namespace()
        self.timers.set('alice', 1, dict(room='room', member='member', user_id='alice'))
        self.now += 60
        ns['_jam_members'].get.return_value = None
        ns['_expire_playback_timer'](self.timers.due()[0])
        ns['_jam_rooms'].transact.assert_not_called()

    def test_cancelled_and_long_overdue_timers_do_not_dispatch(self):
        ns = self.route_namespace()
        self.timers.set('alice', 1, self.target)
        self.now += 60
        timer = self.timers.due()[0]
        self.timers.cancel('alice')
        ns['_expire_playback_timer'](timer)
        self.timers.set('alice', 1, self.target)
        self.now += 400
        ns['time'].time.return_value = self.now
        ns['_expire_playback_timer'](self.timers.due()[0])
        ns['alexa_command'].assert_not_called()


if __name__ == '__main__':
    unittest.main()
