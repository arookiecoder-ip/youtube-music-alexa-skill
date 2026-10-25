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
