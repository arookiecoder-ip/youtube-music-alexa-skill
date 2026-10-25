"""A replacement Amazon account cannot overwrite credentials before verification."""
import ast,os,tempfile,time,asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,AsyncMock
import unittest

class AmazonAccountSwitchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.root=tempfile.TemporaryDirectory();self.addCleanup(self.root.cleanup)
        self.storage=Path(self.root.name);self.stage=self.storage/'pending';(self.stage/'.storage').mkdir(parents=True)
        self.live=self.storage/'.storage'/'account.cookies';self.live.parent.mkdir();self.live.write_text('old-valid-session')
        staged=self.stage/'.storage'/'account.cookies'
        async def save():staged.write_text('new-verified-session')
        self.login=SimpleNamespace(email='new@example.test',_cookiefile=[str(staged)],test_loggedin=AsyncMock(return_value=True),save_cookiefile=save)
        self.old=SimpleNamespace(close=AsyncMock())
        self.owner=SimpleNamespace(_proxy_login=self.login,_login=self.old,_proxy_cookie_stage=str(self.stage),_stop_proxy=AsyncMock())
        path=Path(__file__).resolve().parents[1]/'alexa_remote.py'
        cls=next(n for n in ast.parse(path.read_text()).body if isinstance(n,ast.ClassDef) and n.name=='AlexaRemote')
        method=next(n for n in cls.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='_finalize_proxy_login')
        self.ns=dict(os=os,time=time,asyncio=SimpleNamespace(sleep=AsyncMock()),logger=Mock(),ALEXA_COOKIE_DIR=str(self.storage))
        exec(compile(ast.Module(body=[method],type_ignores=[]),str(path),'exec'),self.ns)
    async def test_verified_replacement_promotes_cookie_and_pointer(self):
        self.assertTrue(await self.ns['_finalize_proxy_login'](self.owner))
        self.assertEqual(self.live.read_text(),'new-verified-session')
        self.assertEqual((self.live.parent/'last_email.txt').read_text(),'new@example.test')
        self.assertIs(self.owner._login,self.login);self.old.close.assert_awaited_once()
        self.assertEqual(self.login._cookiefile,[str(self.live)])
    async def test_failed_verification_keeps_previous_session_and_cookie(self):
        self.login.test_loggedin.return_value=False
        self.assertFalse(await self.ns['_finalize_proxy_login'](self.owner))
        self.assertEqual(self.live.read_text(),'old-valid-session');self.assertIs(self.owner._login,self.old)
        self.old.close.assert_not_awaited()
    async def test_superseded_callback_cannot_commit_its_account(self):
        async def verify():self.owner._proxy_login=object();return True
        self.login.test_loggedin.side_effect=verify
        self.assertFalse(await self.ns['_finalize_proxy_login'](self.owner))
        self.assertEqual(self.live.read_text(),'old-valid-session');self.assertIs(self.owner._login,self.old)

    async def test_cancel_is_bound_to_the_attempt_and_keeps_existing_account(self):
        path=Path(__file__).resolve().parents[1]/'alexa_remote.py'
        cls=next(n for n in ast.parse(path.read_text()).body if isinstance(n,ast.ClassDef) and n.name=='AlexaRemote')
        method=next(n for n in cls.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='_cancel_proxy_attempt')
        exec(compile(ast.Module(body=[method],type_ignores=[]),str(path),'exec'),self.ns)
        self.owner._proxy_attempt_id='new-attempt';self.login.close=AsyncMock()
        cancel=self.ns['_cancel_proxy_attempt']
        self.assertFalse(await cancel(self.owner,'old-attempt'))
        self.owner._stop_proxy.assert_not_awaited()
        self.assertTrue(await cancel(self.owner,'new-attempt'))
        self.assertIs(self.owner._login,self.old)
        self.assertIsNone(self.owner._proxy_login)
        self.assertEqual(self.live.read_text(),'old-valid-session')
        self.login.close.assert_awaited_once()
