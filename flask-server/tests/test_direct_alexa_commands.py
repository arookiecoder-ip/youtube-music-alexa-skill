"""Exercise the real bridge method with only Amazon's network API substituted."""
import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock


class DirectAlexaCommandTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        tree = ast.parse((Path(__file__).resolve().parents[1] / 'alexa_remote.py').read_text())
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'AlexaRemote')
        fn = next(n for n in cls.body if isinstance(n, ast.AsyncFunctionDef) and n.name == '_command')
        namespace = {'asyncio': asyncio, 'logger': Mock(), 'SKILL_INVOCATION_NAME': 'music box',
                     '_TRANSPORT_TEXT': {'play': 'resume', 'pause': 'pause'}}
        exec(compile(ast.Module(body=[fn], type_ignores=[]), 'alexa_remote.py', 'exec'), namespace)
        self.command = namespace['_command']
        self.api = SimpleNamespace(pause=AsyncMock(), run_custom=AsyncMock(), set_volume=AsyncMock())
        self.remote = SimpleNamespace(_api_for=AsyncMock(return_value=(self.api, None)), _login_checked_at=1)

    async def test_pause_uses_native_transport_and_waits_for_its_response(self):
        accepted, acknowledge = asyncio.Event(), asyncio.Event()
        async def pause():
            accepted.set()
            await acknowledge.wait()
        self.api.pause.side_effect = pause
        task = asyncio.create_task(self.command(self.remote, 'echo', 'pause', None))
        await asyncio.wait_for(accepted.wait(), 1)
        self.assertFalse(task.done())
        acknowledge.set()
        self.assertIsNone(await task)
        self.api.run_custom.assert_not_called()

    async def test_rejected_native_pause_is_not_success(self):
        self.api.pause.side_effect = RuntimeError('offline')
        self.assertIsNotNone(await self.command(self.remote, 'echo', 'pause', None))
        self.api.run_custom.assert_not_called()

    async def test_resume_still_uses_the_custom_skill(self):
        self.assertIsNone(await self.command(self.remote, 'echo', 'play', None))
        self.api.run_custom.assert_awaited_once_with('ask music box to resume', queue_delay=0)
        self.api.pause.assert_not_called()

    async def test_volume_has_no_default_batching_delay(self):
        self.assertIsNone(await self.command(self.remote, 'echo', 'volume', 43))
        self.api.set_volume.assert_awaited_once_with(.43, queue_delay=0)
