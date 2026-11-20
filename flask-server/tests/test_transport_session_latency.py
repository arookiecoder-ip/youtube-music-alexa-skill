"""A delayed status recheck must not serialize commands on an established session."""
import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock
from alexa_playback_errors import authentication_failure

class SessionRecheckTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        path=Path(__file__).resolve().parents[1]/'alexa_remote.py'
        cls=next(n for n in ast.parse(path.read_text()).body if isinstance(n,ast.ClassDef) and n.name=='AlexaRemote')
        methods=[n for n in cls.body if isinstance(n,ast.AsyncFunctionDef) and n.name in ('_ensure_login','_refresh_transport_session')]
        self.clock=1000
        ns={'asyncio':asyncio,'time':SimpleNamespace(monotonic=lambda:self.clock),
            '_LOGIN_RECHECK_TTL':300,'authentication_failure':authentication_failure,'logger':Mock()}
        exec(compile(ast.Module(body=methods,type_ignores=[]),str(path),'exec'),ns)
        self.login=SimpleNamespace(email='known@example.test',test_loggedin=AsyncMock(return_value=True),close=AsyncMock())
        self.remote=SimpleNamespace(_login=self.login,_login_checked_at=0,connection_state=True,
            _access_error=Mock(return_value=None),_read_saved_email=Mock(return_value='known@example.test'))
        for n in methods:setattr(self.remote,n.name,ns[n.name].__get__(self.remote))

    async def test_commands_do_not_wait_for_one_shared_blocked_recheck(self):
        entered,release=asyncio.Event(),asyncio.Event()
        async def verify():entered.set();await release.wait();return True
        self.login.test_loggedin.side_effect=verify
        try:
            self.assertIsNone(await asyncio.wait_for(self.remote._ensure_login(background_check=True),.2))
            await asyncio.wait_for(entered.wait(),.2)
            self.assertIsNone(await asyncio.wait_for(self.remote._ensure_login(background_check=True),.2))
            self.login.test_loggedin.assert_awaited_once()
            self.assertFalse(self.remote._transport_session_refresh.done())
            self.remote._access_error.assert_called_with('known@example.test')
        finally:
            release.set()
            await self.remote._transport_session_refresh
        self.assertEqual(self.remote._login_checked_at,1000)
        self.login.close.assert_not_awaited()

    async def test_current_account_denial_still_precedes_any_cookie_use(self):
        self.remote._access_error.return_value='Access was revoked'
        self.assertEqual(await self.remote._ensure_login(background_check=True),'Access was revoked')
        self.assertFalse(self.remote.connection_state)
        self.login.test_loggedin.assert_not_awaited()

    async def blocking_check(self,**kwargs):
        entered,release=asyncio.Event(),asyncio.Event()
        async def verify():entered.set();await release.wait();return True
        self.login.test_loggedin.side_effect=verify
        task=asyncio.create_task(self.remote._ensure_login(**kwargs))
        try:
            await asyncio.wait_for(entered.wait(),.2)
            self.assertFalse(task.done())
            self.assertFalse(hasattr(self.remote,'_transport_session_refresh'))
        finally:
            release.set();self.assertIsNone(await task)

    async def test_known_rejected_session_still_blocks_for_verification(self):
        self.remote.connection_state=False
        await self.blocking_check(background_check=True)

    async def test_explicit_force_check_still_blocks_for_verification(self):
        await self.blocking_check(background_check=True,force_check=True)

    async def test_normal_status_check_still_blocks_for_verification(self):
        await self.blocking_check()

    async def test_negative_recheck_invalidates_trust_without_closing_inflight_client(self):
        self.login.test_loggedin.return_value=False
        await self.remote._ensure_login(background_check=True)
        await self.remote._transport_session_refresh
        self.assertFalse(self.remote.connection_state)
        self.assertEqual(self.remote._login_checked_at,0)
        self.assertIs(self.remote._login,self.login)
        self.login.close.assert_not_awaited()
        self.login.test_loggedin.return_value=True
        await self.remote._ensure_login(background_check=True)
        self.assertTrue(self.remote.connection_state)
        self.assertEqual(self.remote._login_checked_at,1000)

    async def test_transient_failure_keeps_session_and_backs_off_status_retries(self):
        self.login.test_loggedin.side_effect=OSError('temporary network failure')
        await self.remote._ensure_login(background_check=True)
        await self.remote._transport_session_refresh
        await self.remote._ensure_login(background_check=True)
        self.login.test_loggedin.assert_awaited_once()
        self.assertTrue(self.remote.connection_state)
        self.assertEqual(self.remote._login_checked_at,0)
        self.login.close.assert_not_awaited()
        self.clock+=31;self.login.test_loggedin.side_effect=None
        await self.remote._ensure_login(background_check=True)
        await self.remote._transport_session_refresh
        self.assertEqual(self.remote._login_checked_at,self.clock)

    async def test_late_negative_result_cannot_replace_a_reconnected_session(self):
        entered,release=asyncio.Event(),asyncio.Event()
        async def verify():entered.set();await release.wait();return False
        self.login.test_loggedin.side_effect=verify
        await self.remote._ensure_login(background_check=True)
        await asyncio.wait_for(entered.wait(),.2)
        replacement=object();self.remote._login=replacement;self.remote._login_checked_at=999
        release.set();await self.remote._transport_session_refresh
        self.assertIs(self.remote._login,replacement)
        self.assertTrue(self.remote.connection_state)
        self.assertEqual(self.remote._login_checked_at,999)
