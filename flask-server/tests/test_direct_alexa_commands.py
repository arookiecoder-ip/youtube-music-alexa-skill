from alexa_transport import bounded_transport, TransportDeferred
"""Exercise the real bridge method with only Amazon's network API substituted."""
import ast
import asyncio
import time
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock
from alexa_playback_errors import authentication_failure


class DirectAlexaCommandTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        tree = ast.parse((Path(__file__).resolve().parents[1] / 'alexa_remote.py').read_text())
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'AlexaRemote')
        fn = next(n for n in cls.body if isinstance(n, ast.AsyncFunctionDef) and n.name == '_command')
        namespace = {'bounded_transport': bounded_transport, 'TransportDeferred': TransportDeferred, 'authentication_failure': authentication_failure, 'asyncio': asyncio, 'time': time, 'logger': Mock(), 'SKILL_INVOCATION_NAME': 'music box',
                     '_TRANSPORT_TEXT': {'play': 'resume', 'pause': 'pause'}}
        exec(compile(ast.Module(body=[fn], type_ignores=[]), 'alexa_remote.py', 'exec'), namespace)
        self.command = namespace['_command']
        self.api = SimpleNamespace(pause=AsyncMock(), run_custom=AsyncMock(), set_volume=AsyncMock())
        self.remote = SimpleNamespace(_api_for=AsyncMock(return_value=(self.api, None)), _login_checked_at=1)

    async def test_pause_uses_custom_skill_and_waits_for_its_response(self):
        accepted, acknowledge = asyncio.Event(), asyncio.Event()
        async def pause(*args, **kwargs):
            accepted.set()
            await acknowledge.wait()
        self.api.run_custom.side_effect = pause
        task = asyncio.create_task(self.command(self.remote, 'echo', 'pause', None))
        await asyncio.wait_for(accepted.wait(), 1)
        self.assertFalse(task.done())
        acknowledge.set()
        self.assertIsNone(await task)
        self.api.pause.assert_not_called()
        self.api.run_custom.assert_awaited_once_with('ask music box to pause', queue_delay=0)

    async def test_rejected_skill_pause_is_not_success(self):
        self.api.run_custom.side_effect = RuntimeError('offline')
        self.assertIsNotNone(await self.command(self.remote, 'echo', 'pause', None))
        self.api.pause.assert_not_called()

    async def test_resume_still_uses_the_custom_skill(self):
        self.assertIsNone(await self.command(self.remote, 'echo', 'play', None))
        self.api.run_custom.assert_awaited_once_with('ask music box to resume', queue_delay=0)
        self.api.pause.assert_not_called()

    async def test_volume_has_no_default_batching_delay(self):
        self.assertIsNone(await self.command(self.remote, 'echo', 'volume', 43))
        self.api.set_volume.assert_awaited_once_with(.43, queue_delay=0)


    async def test_authentication_rejection_has_reconnect_message(self):
        self.api.run_custom.side_effect = type('AlexapyLoginError', (Exception,), {})('expired')
        error = await self.command(self.remote, 'echo-one', 'play', None)
        self.assertIn('Reconnect Amazon', error)
        self.assertFalse(self.remote.connection_state)

    async def test_network_error_does_not_expire_valid_authentication(self):
        self.api.run_custom.side_effect=RuntimeError('network unavailable')
        self.assertIsNotNone(await self.command(self.remote,'echo','play',None))
        self.assertEqual(self.remote._login_checked_at,1)

    async def test_rate_limit_does_not_expire_valid_authentication(self):
        self.api.run_custom.side_effect=type('AlexapyTooManyRequestsError',(Exception,),{})('Too Many Requests')
        self.assertIn('rate-limiting',await self.command(self.remote,'echo','play',None))
        self.assertEqual(self.remote._login_checked_at,1)


class ControlLaunchTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await DirectAlexaCommandTests.asyncSetUp(self)
    async def test_verified_new_skill_resume_uses_launch_without_voice_or_native_play(self):
        self.remote.control_skill_id=Mock(return_value='amzn1.ask.skill.11111111-1111-1111-1111-111111111111')
        self.api.run_skill=AsyncMock()
        self.assertIsNone(await self.command(self.remote,'echo','play',None))
        self.api.run_skill.assert_awaited_once_with(self.remote.control_skill_id(),queue_delay=0)
        self.api.run_custom.assert_not_awaited()

    async def test_pause_keeps_existing_path_even_after_control_discovery(self):
        self.remote.control_skill_id=Mock(return_value='amzn1.ask.skill.11111111-1111-1111-1111-111111111111')
        self.api.run_skill=AsyncMock()
        self.assertIsNone(await self.command(self.remote,'echo','pause',None))
        self.api.run_skill.assert_not_awaited()
        self.api.run_custom.assert_awaited_once_with('ask music box to pause',queue_delay=0)

    async def test_missing_or_disabled_control_capability_keeps_voice_resume(self):
        self.remote.control_skill_id=Mock(return_value=None)
        self.api.run_skill=AsyncMock()
        self.assertIsNone(await self.command(self.remote,'echo','play',None))
        self.api.run_skill.assert_not_awaited()
        self.api.run_custom.assert_awaited_once_with('ask music box to resume',queue_delay=0)


class ControlSkillDiscoveryTests(unittest.TestCase):
    def setUp(self):
        import re
        tree=ast.parse((Path(__file__).resolve().parents[1]/'alexa_remote.py').read_text())
        cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='AlexaRemote')
        methods=[n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name in ('remember_control_skill','control_skill_id','disable_control_launch')]
        namespace={'re':re}
        exec(compile(ast.Module(body=methods,type_ignores=[]),'alexa_remote.py','exec'),namespace)
        self.factory=type('Remote',(),{n.name:namespace[n.name] for n in methods})
        self.remote=self.factory()
        self.skill='amzn1.ask.skill.11111111-1111-1111-1111-111111111111'

    def test_invalid_and_ambiguous_skills_do_not_enable_launch(self):
        for value in (None,'',123,'other','amzn1.ask.skill.'+'-'*36):
            self.remote.remember_control_skill(value)
        self.assertIsNone(self.remote.control_skill_id())
        self.remote.remember_control_skill(self.skill)
        self.assertEqual(self.remote.control_skill_id(),self.skill)
        self.remote.remember_control_skill('amzn1.ask.skill.22222222-2222-2222-2222-222222222222')
        self.assertIsNone(self.remote.control_skill_id())

    def test_watchdog_disable_survives_further_discovery_and_is_account_local(self):
        self.remote.remember_control_skill(self.skill)
        self.remote.disable_control_launch()
        self.remote.remember_control_skill(self.skill)
        self.assertIsNone(self.remote.control_skill_id())
        other=self.factory();other.remember_control_skill(self.skill)
        self.assertEqual(other.control_skill_id(),self.skill)
