"""Owner boundaries, CSRF and expiry using the real Flask routes and a disposable DB."""
import ast
import os
import hmac
import asyncio
from pathlib import Path
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, AsyncMock, patch
from aiohttp import web
from aiohttp.test_utils import make_mocked_request
from test_audio_endpoint import server


class OwnerSecurityTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(); self.addCleanup(self.directory.cleanup)
        for name, value in [('DB_FILE', str(Path(self.directory.name)/'sessions.db')),
                            ('_db_initialized', False), ('_valid_sids', None), ('_sid_deadlines', {}),
                            ('_pending_2fa', None)]:
            p = patch.object(server, name, value); p.start(); self.addCleanup(p.stop)
        server._ensure_db()
        self.owner = server.app.test_client()
        with server.app.test_request_context('/'):
            self.sid = server._session_open()
        with self.owner.session_transaction() as session:
            session['remote_user'] = server.REMOTE_USER
            session['sid'] = self.sid
        self.anonymous = server.app.test_client()

    def test_empty_runtime_key_never_bypasses_owner_authorization(self):
        with patch.object(server, 'API_KEY', ''):
            self.assertEqual(self.anonymous.get('/api/library/').status_code, 401)
            self.assertEqual(self.owner.get('/audio/?video_id=bad%20id').status_code, 400)

    def test_absolute_expiry_applies_to_web_and_audio_without_restart(self):
        self.assertTrue(server._audio_session_valid(self.sid))
        with patch.object(server.time, 'time', return_value=time.time()+31*86400):
            self.assertFalse(server._audio_session_valid(self.sid))
            self.assertEqual(self.owner.get('/alexa/status/').status_code, 401)

    def test_get_and_plain_form_logout_do_not_revoke_owner(self):
        with patch.object(server, '_jam_close_all') as close_jam:
            self.assertEqual(self.owner.get('/logout/').status_code, 302)
            self.assertTrue(server._audio_session_valid(self.sid))
            self.assertEqual(self.owner.post('/logout/', data={'everywhere': '1'}).status_code, 415)
            close_jam.assert_not_called()
        with patch.object(server._youtube_browser_sessions, 'revoke', create=True):
            self.assertEqual(self.owner.post('/logout/', json={}).status_code, 200)
        self.assertFalse(server._audio_session_valid(self.sid))

    def test_owner_cookie_cannot_change_likes_by_get(self):
        with patch.object(server, '_get_ytmusic_home') as yt:
            self.assertEqual(self.owner.get('/alexa/like/?video_id=aaaaaaaaaaa').status_code, 405)
            yt.assert_not_called()

    def test_proxy_requires_matching_attempt_and_live_owner_sid(self):
        cookie = self.owner.get_cookie(server.app.config['SESSION_COOKIE_NAME'])
        header = server.app.config['SESSION_COOKIE_NAME']+'='+cookie.value
        remote = SimpleNamespace(_proxy_owner_sid=self.sid, _proxy_attempt_id='current-attempt')
        with patch.object(server.alexa_remote, 'remote', remote):
            # This cookie has no attempt marker, as with a concurrent refresh
            # that began before the login attempt was created.
            self.assertTrue(server._authorize_amazon_proxy(header, 'current-attempt'))
            self.assertFalse(server._authorize_amazon_proxy(header, 'different-attempt'))
            self.assertFalse(server._authorize_amazon_proxy('', 'current-attempt'))
            remote._proxy_owner_sid='different-owner-session'
            self.assertFalse(server._authorize_amazon_proxy(header, 'current-attempt'))
            remote._proxy_owner_sid=self.sid
            server._session_close(self.sid)
            self.assertFalse(server._authorize_amazon_proxy(header, 'current-attempt'))

    def test_proxy_start_binds_the_real_owner_sid_without_a_mutable_cookie_marker(self):
        remote = SimpleNamespace(_proxy_owner_sid=None, _proxy_attempt_id='new-attempt', is_logged_in=lambda:False)
        def start(email, password, base_url=None, owner_sid=None):
            remote._proxy_owner_sid=owner_sid
            return 'https://example.test/alexa/proxy/',None
        remote.proxy_start_url=start
        with patch.object(server.alexa_remote, 'remote', remote):
            result=self.owner.post('/alexa/proxy_login/',json={'email':'owner@example.test','password':'dummy'})
            self.assertEqual(result.status_code,200)
            self.assertEqual(remote._proxy_owner_sid,self.sid)
            cookie=self.owner.get_cookie(server.app.config['SESSION_COOKIE_NAME'])
            header=server.app.config['SESSION_COOKIE_NAME']+'='+cookie.value
            self.assertTrue(server._authorize_amazon_proxy(header,'new-attempt'))
            with self.owner.session_transaction() as session:
                self.assertNotIn('amazon_login_attempt',session)

    def test_password_verified_challenges_are_pruned_and_bounded(self):
        with server.app.test_request_context('/'):
            store, bind = server._pending_2fa_door()
        with patch('multiuser.pending.time.time', return_value=time.time()-301):
            expired = store.issue({'kind':'owner', 'user':server.REMOTE_USER}, bind)
        store.issue({'kind':'owner', 'user':server.REMOTE_USER}, bind)
        self.assertIsNone(store.consume(expired, bind))
        for _ in range(store.capacity + 5):
            store.issue({'kind':'owner', 'user':server.REMOTE_USER}, bind)
        self.assertEqual(store.waiting(), store.capacity)
        with patch.object(server, '_totp_enabled', return_value=True):
            result = self.anonymous.post('/login/', json={'username':server.REMOTE_USER, 'password':server.REMOTE_PASSWORD})
        self.assertEqual(result.status_code, 200)
        self.assertTrue(result.get_json()['totp_required'])
        self.assertEqual(store.waiting(), store.capacity)



class AmazonProxyBoundaryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        path = Path(__file__).resolve().parents[1]/'alexa_remote.py'
        cls = next(n for n in ast.parse(path.read_text()).body if isinstance(n, ast.ClassDef) and n.name=='AlexaRemote')
        node = next(n for n in cls.body if isinstance(n, ast.AsyncFunctionDef) and n.name=='_proxy_handler')
        ns = dict(web=web, asyncio=asyncio, time=time, os=os, hmac=hmac)
        exec(compile(ast.Module(body=[node], type_ignores=[]),str(path),'exec'), ns)
        self.handler = ns['_proxy_handler']
        self.forward = AsyncMock(return_value=web.Response(text='dummy login page'))
        self.owner = SimpleNamespace(_proxy=SimpleNamespace(all_handler=self.forward),
            _proxy_login=None, _proxy_authorizer=None, _proxy_attempt_id='attempt', _proxy_attempt_started=time.monotonic())

    async def test_missing_authority_and_anonymous_requests_fail_closed(self):
        req=make_mocked_request('GET','/alexa/proxy/stop')
        with self.assertRaises(web.HTTPForbidden): await self.handler(self.owner, req)
        self.owner._proxy_authorizer=lambda cookie, attempt: cookie=='session=owner'
        with self.assertRaises(web.HTTPForbidden): await self.handler(self.owner, req)
        self.forward.assert_not_awaited()

    async def test_valid_owner_can_continue_but_expired_attempt_is_rejected(self):
        self.owner._proxy_authorizer=lambda cookie, attempt: cookie=='session=owner' and attempt=='attempt'
        req=make_mocked_request('GET','/alexa/proxy/',headers={'Cookie':'session=owner'})
        self.assertEqual((await self.handler(self.owner,req)).status,200)
        self.owner._proxy_attempt_started-=601
        with self.assertRaises(web.HTTPForbidden): await self.handler(self.owner,req)
        self.assertEqual(self.forward.await_count,1)

    async def test_superseded_attempt_cannot_forward_to_a_new_shared_proxy(self):
        def authorize(cookie, attempt):
            self.owner._proxy_attempt_id='replacement-attempt'
            return True
        self.owner._proxy_authorizer=authorize
        req=make_mocked_request('GET','/alexa/proxy/',headers={'Cookie':'session=owner'})
        with self.assertRaises(web.HTTPForbidden): await self.handler(self.owner,req)
        self.forward.assert_not_awaited()
