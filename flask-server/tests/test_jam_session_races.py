"""Use production cookie settings to reproduce a read racing a Jam join."""
import ast
from datetime import timedelta
from pathlib import Path
import threading
import unittest
from flask import Flask, jsonify, session


class JamSessionCookieTests(unittest.TestCase):
    def test_old_read_response_cannot_overwrite_join_cookie(self):
        app = Flask(__name__)
        app.secret_key = 'isolated-cookie-race-fixture'
        tree = ast.parse((Path(__file__).resolve().parents[1] / 'server.py').read_text())
        settings = next(node for node in tree.body if isinstance(node, ast.Expr)
                        and isinstance(node.value, ast.Call)
                        and any(keyword.arg == 'PERMANENT_SESSION_LIFETIME' for keyword in node.value.keywords))
        exec(compile(ast.Module(body=[settings], type_ignores=[]), 'server.py', 'exec'),
             dict(app=app, _session_cookie_secure=True, timedelta=timedelta))
        entered, release = threading.Event(), threading.Event()
        @app.route('/login')
        def login():
            session['sid'] = 'test-account'
            session.permanent = True
            return jsonify(ok=True)
        @app.route('/read')
        def read():
            _ = session.get('jam')
            entered.set()
            self.assertTrue(release.wait(2))
            return jsonify(ok=True)
        @app.route('/join')
        def join():
            session['jam'] = 'room'
            session['jam_member'] = 'member'
            return jsonify(ok=True)
        @app.route('/state')
        def state():
            return jsonify(active=session.get('jam') == 'room')
        joiner = app.test_client()
        joiner.get('/login', base_url='https://example.test')
        cookie = joiner.get_cookie('session', domain='example.test').value
        old_reader = app.test_client()
        old_reader.set_cookie('session', cookie, domain='example.test')
        replies = []
        worker = threading.Thread(target=lambda: replies.append(old_reader.get('/read', base_url='https://example.test')))
        worker.start()
        self.assertTrue(entered.wait(1))
        joined = joiner.get('/join', base_url='https://example.test')
        self.assertIn('Set-Cookie', joined.headers)
        release.set()
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertNotIn('Set-Cookie', replies[0].headers)
        self.assertTrue(joiner.get('/state', base_url='https://example.test').json['active'])
