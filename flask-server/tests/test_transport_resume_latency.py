"""Known-online resumes dispatch while stale presence refreshes in parallel."""
import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock


class TransportResumeLatencyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        source = Path(__file__).resolve().parents[1] / 'alexa_remote.py'
        cls = next(n for n in ast.parse(source.read_text()).body if isinstance(n, ast.ClassDef) and n.name == 'AlexaRemote')
        methods = [n for n in cls.body if isinstance(n, ast.AsyncFunctionDef) and n.name in ('_api_for', '_command', '_refresh_transport_presence', '_pause_for_handoff')]
        self.api = SimpleNamespace(run_custom=AsyncMock(), pause=AsyncMock())
        self.raw = {'online': True, 'accountName': 'Echo'}
        self.ns = dict(asyncio=asyncio, time=SimpleNamespace(monotonic=lambda: 100), _DEVICE_STATE_TTL=30,
            AlexaAPI=lambda device, login: self.api, logger=Mock(), SKILL_INVOCATION_NAME='music box',
            _TRANSPORT_TEXT={'play': 'resume', 'pause': 'pause'}, authentication_failure=lambda error: None)
        exec(compile(ast.Module(body=methods, type_ignores=[]), str(source), 'exec'), self.ns)
        self.owner = SimpleNamespace(_ensure_login=AsyncMock(return_value=None), _devices_raw=[self.raw],
            _devices_fetched_at=0, _login=object(), _find_device=lambda serial: object(),
            _find_device_raw=lambda serial: self.raw)
        self.owner._api_for = self.ns['_api_for'].__get__(self.owner)
        self.owner._refresh_transport_presence = self.ns['_refresh_transport_presence'].__get__(self.owner)

    async def test_resume_never_waits_for_stale_known_online_presence(self):
        release = asyncio.Event()
        async def refresh(**kwargs):
            await release.wait()
            return [], None
        self.owner._devices = AsyncMock(side_effect=refresh)
        try:
            result = await asyncio.wait_for(self.ns['_command'](self.owner, 'echo', 'play', None), .2)
            self.assertIsNone(result)
            self.api.run_custom.assert_awaited_once_with('ask music box to resume', queue_delay=0)
            await asyncio.sleep(0)
            self.assertFalse(self.owner._transport_presence_refresh.done())
            await self.ns['_command'](self.owner, 'echo', 'pause', None)
            self.assertEqual(self.owner._devices.await_count, 1, 'Rapid commands share one background refresh')
        finally:
            release.set()
            await self.owner._transport_presence_refresh

    async def test_known_offline_device_still_requires_synchronous_verification(self):
        self.raw['online'] = False
        self.owner._devices = AsyncMock(return_value=([], None))
        error = await self.ns['_command'](self.owner, 'echo', 'play', None)
        self.assertIn('offline', error)
        self.owner._devices.assert_awaited_once_with(refresh=True)
        self.api.run_custom.assert_not_awaited()

    async def test_failed_background_refresh_does_not_reject_an_accepted_resume(self):
        self.owner._devices = AsyncMock(side_effect=RuntimeError('presence unavailable'))
        self.assertIsNone(await self.ns['_command'](self.owner, 'echo', 'play', None))
        await self.owner._transport_presence_refresh
        self.api.run_custom.assert_awaited_once()

    async def test_handoff_uses_native_pause_without_skill_invocation(self):
        self.owner._devices_fetched_at = 100
        self.assertIsNone(await self.ns['_pause_for_handoff'](self.owner, 'echo'))
        self.api.pause.assert_awaited_once_with()
        self.api.run_custom.assert_not_awaited()

    async def test_unsupported_native_pause_falls_back_to_the_skill(self):
        self.owner._devices_fetched_at = 100
        self.owner._command = self.ns['_command'].__get__(self.owner)
        self.api.pause.side_effect = RuntimeError('unsupported firmware')
        self.assertIsNone(await self.ns['_pause_for_handoff'](self.owner, 'echo'))
        self.api.run_custom.assert_awaited_once_with('ask music box to pause', queue_delay=0)
