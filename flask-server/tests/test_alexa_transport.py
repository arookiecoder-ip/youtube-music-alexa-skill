"""Real command gate: bursts, cancellation, cooldowns and account isolation."""
import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock
from alexa_transport import AmazonTransport, TransportDeferred, bounded_transport


class AmazonTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_identical_inflight_pause_is_sent_once(self):
        gate=AmazonTransport();started=asyncio.Event();release=asyncio.Event();calls=[]
        async def send():
            calls.append('pause');started.set();await release.wait()
        tasks=[asyncio.create_task(gate.run('echo','pause',send)) for _ in range(40)]
        await started.wait();await asyncio.sleep(0);self.assertEqual(calls,['pause'])
        release.set();self.assertEqual(await asyncio.gather(*tasks),[None]*40)

    async def test_newest_queued_transport_wins_without_reordering_running_call(self):
        gate=AmazonTransport();started=asyncio.Event();release=asyncio.Event();calls=[]
        async def first():
            calls.append('initial');started.set();await release.wait()
        initial=asyncio.create_task(gate.run('echo','initial',first));await started.wait()
        async def send(value):calls.append(value)
        obsolete=asyncio.create_task(gate.run('echo','play',lambda:send('play')))
        await asyncio.sleep(0)
        newest=asyncio.create_task(gate.run('echo','pause',lambda:send('pause')))
        await asyncio.sleep(0);release.set()
        await initial
        with self.assertRaises(TransportDeferred):await obsolete
        await newest;self.assertEqual(calls,['initial','pause'])

    async def test_timeout_cancels_backoff_and_watchdog_cannot_send_during_cooldown(self):
        now=[100.0];gate=AmazonTransport(timeout=.01,clock=lambda:now[0]);cancelled=asyncio.Event();calls=[]
        async def stalled():
            calls.append('send')
            try:await asyncio.Event().wait()
            finally:cancelled.set()
        with self.assertRaises(TransportDeferred):await gate.run('echo','play',stalled)
        self.assertTrue(cancelled.is_set())
        for key in ['watchdog','resume','seek']:
            with self.assertRaises(TransportDeferred):await gate.run('echo',key,stalled)
        self.assertEqual(calls,['send'])
        now[0]+=16
        send=AsyncMock();await gate.run('echo','resume',send);send.assert_awaited_once()

    async def test_rate_limit_cooldown_is_shared_by_devices_but_not_accounts(self):
        first,second=AmazonTransport(),AmazonTransport()
        error=type('AlexapyTooManyRequestsError',(Exception,),{})('Too Many Requests')
        send=AsyncMock(side_effect=error)
        with self.assertRaises(TransportDeferred):await first.run('echo-one','play',send)
        other=AsyncMock()
        with self.assertRaises(TransportDeferred):await first.run('echo-two','play',other)
        other.assert_not_awaited()
        await second.run('echo-one','play',other);other.assert_awaited_once()

    async def test_cancelling_one_waiter_preserves_shared_send_but_last_waiter_cancels_it(self):
        gate=AmazonTransport();started=asyncio.Event();cancelled=asyncio.Event()
        async def send():
            started.set()
            try:await asyncio.Event().wait()
            finally:cancelled.set()
        first=asyncio.create_task(gate.run('echo','play',send));await started.wait()
        second=asyncio.create_task(gate.run('echo','play',send));await asyncio.sleep(0)
        first.cancel()
        with self.assertRaises(asyncio.CancelledError):await first
        self.assertFalse(cancelled.is_set())
        second.cancel()
        with self.assertRaises(asyncio.CancelledError):await second
        self.assertTrue(cancelled.is_set())
        next_send=AsyncMock();await gate.run('echo','pause',next_send);next_send.assert_awaited_once()

    async def test_authentication_rejection_is_not_cached_as_rate_limit(self):
        gate=AmazonTransport();error=type('AlexapyLoginError',(Exception,),{})('expired')
        with self.assertRaises(type(error)):await gate.run('echo','play',AsyncMock(side_effect=error))
        self.assertEqual(gate.cooldown_until,0)

    async def test_transient_login_verification_does_not_close_live_session(self):
        source=Path(__file__).resolve().parents[1]/'alexa_remote.py'
        cls=next(n for n in ast.parse(source.read_text()).body if isinstance(n,ast.ClassDef) and n.name=='AlexaRemote')
        method=next(n for n in cls.body if getattr(n,'name','')=='_ensure_login')
        ns=dict(asyncio=asyncio,time=SimpleNamespace(monotonic=lambda:1000),_LOGIN_RECHECK_TTL=300,
                authentication_failure=lambda exc:None,logger=Mock())
        exec(compile(ast.Module(body=[method],type_ignores=[]),str(source),'exec'),ns)
        login=SimpleNamespace(email='private@example.invalid',test_loggedin=AsyncMock(side_effect=TimeoutError()),close=AsyncMock())
        owner=SimpleNamespace(_login=login,_login_checked_at=1,_access_error=lambda email:None,connection_state=True)
        error=await ns['_ensure_login'](owner)
        self.assertIn('temporarily unavailable',error);self.assertIs(owner._login,login)
        self.assertTrue(owner.connection_state);login.close.assert_not_awaited()
