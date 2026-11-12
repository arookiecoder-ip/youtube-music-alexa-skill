import asyncio
import os
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
from network_sessions import ProxyAwareAlexaLogin


class NetworkSessionTests(unittest.IsolatedAsyncioTestCase):
    async def test_new_alexa_session_honors_egress_and_keeps_cookies_on_reuse(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {'HTTPS_PROXY':'http://gateway.test:8890'}):
            login = ProxyAwareAlexaLogin('amazon.in', 'test@example.com', '', lambda name: str(Path(root)/name))
            try:
                session = login.session
                self.assertTrue(session.trust_env)
                login._create_session()
                self.assertIs(login.session, session)
                await asyncio.sleep(0)
            finally:
                await login.close()

    async def test_failed_restore_reset_preserves_persisted_credentials(self):
        with tempfile.TemporaryDirectory() as root:
            login = ProxyAwareAlexaLogin('amazon.in', 'test@example.com', '', lambda name: str(Path(root)/name))
            saved = Path(login._cookiefile[0]); saved.parent.mkdir(parents=True, exist_ok=True)
            saved.write_text('persisted-test-session')
            async def failed_login(**kwargs):
                await login.reset()
            try:
                from unittest.mock import AsyncMock
                with patch.object(login, 'load_cookie', AsyncMock(return_value={'session':'test'})), patch.object(login, 'login', failed_login):
                    self.assertFalse(await login.restore_cookie_session())
                self.assertEqual(saved.read_text(), 'persisted-test-session')
                self.assertFalse(login._restoring_saved_session)
                await login.reset()
                self.assertFalse(saved.exists())  # Explicit logout/reset still clears it.
            finally:
                await login.close()
                await asyncio.sleep(0)

    async def test_restore_exception_clears_guard_without_losing_cookie(self):
        from unittest.mock import AsyncMock
        with tempfile.TemporaryDirectory() as root:
            login = ProxyAwareAlexaLogin('amazon.in', 'test@example.com', '', lambda name: str(Path(root)/name))
            saved = Path(login._cookiefile[0]); saved.parent.mkdir(parents=True, exist_ok=True); saved.write_text('saved')
            try:
                with patch.object(login, 'load_cookie', AsyncMock(return_value={'session':'test'})), patch.object(login, 'login', AsyncMock(side_effect=TimeoutError)):
                    with self.assertRaises(TimeoutError):
                        await login.restore_cookie_session()
                self.assertEqual(saved.read_text(), 'saved')
                self.assertFalse(login._restoring_saved_session)
            finally:
                await login.close()

    async def test_oauth_callback_is_scoped_to_trusted_callback_locations(self):
        with tempfile.TemporaryDirectory() as root:
            login=ProxyAwareAlexaLogin('amazon.in','test@example.test','',lambda name:str(Path(root)/name))
            other=ProxyAwareAlexaLogin('amazon.in','other@example.test','',lambda name:str(Path(root)/name))
            try:
                for url in ('http://www.amazon.com/ap/maplanding?openid.oa2.authorization_code=untrusted',
                            'https://evil.example/ap/maplanding?openid.oa2.authorization_code=untrusted',
                            'https://www.amazon.com/ap/signin?openid.oa2.authorization_code=untrusted'):
                    self.assertFalse(login._capture_oauth_callback(url))
                    self.assertFalse(login.authorization_code)
                self.assertTrue(login._capture_oauth_callback('https://www.amazon.com/ap/maplanding?openid.oa2.authorization_code=synthetic-code'))
                self.assertEqual(login.authorization_code,'synthetic-code')
                self.assertFalse(other.authorization_code)
            finally:
                await login.close();await other.close();await asyncio.sleep(0)
