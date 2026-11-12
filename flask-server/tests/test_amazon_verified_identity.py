"""Security boundary: canonical identity comes from Amazon, never a login field."""
import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, Mock


class VerifiedAmazonIdentityTests(IsolatedAsyncioTestCase):
    def setUp(self):
        path = Path(__file__).resolve().parents[1] / 'alexa_remote.py'
        source = ast.parse(path.read_text())
        cls = next(n for n in source.body if isinstance(n, ast.ClassDef) and n.name == 'AlexaRemote')
        cls.body = [n for n in cls.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and n.name in {'_verified_account', '_status', '_logout'}]
        self.api = SimpleNamespace(get_authentication=AsyncMock(return_value={
            'authenticated': True, 'customerEmail': 'verified@example.test', 'customerId': 'customer-B'}))
        namespace = {'AlexaAPI': self.api, 'OWNER': 'owner', 'logger': Mock(),
                     '_release_proxy_claim': Mock()}
        exec(compile(ast.Module(body=[cls], type_ignores=[]), str(path), 'exec'), namespace)
        self.remote = namespace['AlexaRemote']()
        self.login = SimpleNamespace(email='+919876543210', close=AsyncMock())
        self.remote._login = self.login
        self.remote._musicbox_identity = 'user:explicit-id'
        self.remote._identity_sink = Mock()
        self.remote._ensure_login = AsyncMock(return_value=None)
        self.remote._access_error = Mock(return_value=None)
        self.remote._status_result = lambda error=None: {'logged_in': error is None}

    async def test_saved_session_status_verifies_canonical_identity(self):
        await self.remote._status()
        self.remote._identity_sink.assert_called_once_with(
            'user:explicit-id', 'verified@example.test', 'customer-B')
        await self.remote._status()
        self.api.get_authentication.assert_awaited_once_with(self.login)

    async def test_untrusted_or_missing_profile_does_not_publish_login_identifier(self):
        for profile in (None, {}, {'authenticated': False},
                        {'authenticated': True, 'customerEmail': 'claim@example.test'},
                        {'authenticated': True, 'customerEmail': 1, 'customerId': 'id'}):
            self.api.get_authentication.return_value = profile
            result = await self.remote._status()
            self.assertIn('identity_error', result)
        self.remote._identity_sink.assert_not_called()

    async def test_provider_failure_can_retry_without_trusting_login_field(self):
        self.api.get_authentication.side_effect = RuntimeError('Provider unavailable')
        self.assertIn('identity_error', await self.remote._status())
        self.remote._identity_sink.assert_not_called()
        self.api.get_authentication.side_effect = None
        await self.remote._status()
        self.remote._identity_sink.assert_called_once()

    async def test_status_result_for_replaced_login_cannot_overwrite_new_account(self):
        async def replaced(login):
            self.remote._login = object()
            return {'authenticated': True, 'customerEmail': 'stale@example.test', 'customerId': 'old'}
        self.api.get_authentication.side_effect = replaced
        self.assertIn('identity_error', await self.remote._status())
        self.remote._identity_sink.assert_not_called()

    async def test_logout_fences_profile_lookup_before_network_cleanup(self):
        started, proceed = asyncio.Event(), asyncio.Event()
        async def delayed(login):
            started.set()
            await proceed.wait()
            return {'authenticated': True, 'customerEmail': 'stale@example.test', 'customerId': 'old'}
        self.api.get_authentication.side_effect = delayed
        status = asyncio.create_task(self.remote._status())
        await started.wait()
        self.remote._proxy_login = self.remote._proxy_finalize_task = None
        self.remote._proxy_runner = None
        self.remote._clear_persisted_session = Mock()
        self.remote._form_cleanup = AsyncMock()
        await self.remote._logout()
        proceed.set()
        await status
        self.remote._identity_sink.assert_called_once_with('user:explicit-id', None, None)
        self.assertIsNone(self.remote._login)
