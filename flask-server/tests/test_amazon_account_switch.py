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
        storage=str(self.storage)
        self.owner=SimpleNamespace(_access_error=Mock(return_value=None),cookie_dir=storage,_storage_dir=os.path.join(storage,'.storage'),
            _email_file=os.path.join(storage,'.storage','last_email.txt'),
            _proxy_login=self.login,_login=self.old,_proxy_cookie_stage=str(self.stage),_stop_proxy=AsyncMock())
        path=Path(__file__).resolve().parents[1]/'alexa_remote.py'
        cls=next(n for n in ast.parse(path.read_text()).body if isinstance(n,ast.ClassDef) and n.name=='AlexaRemote')
        method=next(n for n in cls.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='_finalize_proxy_login')
        promote=next(n for n in cls.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='_promote_staged_login')
        self.ns=dict(OWNER="owner",os=os,time=time,tempfile=tempfile,asyncio=SimpleNamespace(sleep=AsyncMock()),logger=Mock())
        exec(compile(ast.Module(body=[method,promote],type_ignores=[]),str(path),'exec'),self.ns)
        self.owner._promote_staged_login=self.ns['_promote_staged_login'].__get__(self.owner)
    async def test_verified_replacement_promotes_cookie_and_pointer(self):
        self.assertTrue(await self.ns['_finalize_proxy_login'](self.owner))
        self.assertEqual(self.live.read_text(),'new-verified-session')
        self.assertEqual((self.live.parent/'last_email.txt').read_text(),'new@example.test')
        self.assertIs(self.owner._login,self.login);self.old.close.assert_awaited_once()
        self.assertEqual(self.login._cookiefile,[str(self.live)])
    async def test_admin_revocation_before_promotion_preserves_existing_cookie(self):
        self.owner._access_error.return_value = 'Amazon access disabled'
        error = await self.owner._promote_staged_login(self.login, str(self.stage))
        self.assertEqual(error, 'Amazon access disabled')
        self.assertEqual(self.live.read_text(), 'old-valid-session')
        self.assertIs(self.owner._login, self.old)

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

    def enable_identity_hook(self):
        pointer = Path(self.owner._email_file)
        pointer.write_text('old@example.test')
        self.owner._read_saved_email = lambda: pointer.read_text()
        self.owner._identity_sink = Mock()
        self.owner._musicbox_identity = 'user:explicit-owner'
        self.owner._verified_account = AsyncMock(return_value={
            'customerEmail': 'canonical@example.test', 'customerId': 'amazon-customer'})

    async def test_phone_login_publishes_canonical_authenticated_amazon_email(self):
        self.enable_identity_hook()
        self.login.email = '+919876543210'
        error = await self.owner._promote_staged_login(self.login, str(self.stage))
        self.assertIsNone(error)
        self.owner._identity_sink.assert_called_once_with(
            'user:explicit-owner', 'canonical@example.test', 'amazon-customer')
        self.assertEqual(Path(self.owner._email_file).read_text(), self.login.email)

    async def test_missing_canonical_identity_preserves_previous_account(self):
        self.enable_identity_hook()
        self.owner._verified_account.return_value = None
        self.assertIsNotNone(await self.owner._promote_staged_login(self.login, str(self.stage)))
        self.owner._identity_sink.assert_not_called()
        self.assertEqual(self.live.read_text(), 'old-valid-session')

    async def test_cancellation_during_identity_lookup_cannot_promote(self):
        self.enable_identity_hook()
        async def cancelled(login):
            self.owner._proxy_login = None
            return {'customerEmail':'canonical@example.test', 'customerId':'amazon-customer'}
        self.owner._verified_account.side_effect = cancelled
        self.assertIsNotNone(await self.owner._promote_staged_login(self.login, str(self.stage)))
        self.owner._identity_sink.assert_not_called()
        self.assertEqual(self.live.read_text(), 'old-valid-session')

    async def test_revocation_during_cookie_save_cannot_promote(self):
        self.enable_identity_hook()
        original = self.login.save_cookiefile
        async def revoked():
            await original()
            self.owner._access_error.return_value = 'Access disabled'
        self.login.save_cookiefile = revoked
        self.assertEqual(await self.owner._promote_staged_login(self.login, str(self.stage)), 'Access disabled')
        self.owner._identity_sink.assert_not_called()
        self.assertEqual(self.live.read_text(), 'old-valid-session')

    async def test_identity_commit_failure_restores_existing_cookie_and_pointer(self):
        self.enable_identity_hook()
        self.owner._identity_sink.side_effect = RuntimeError('Database write refused')
        self.assertIsNotNone(await self.owner._promote_staged_login(self.login, str(self.stage)))
        self.assertEqual(self.live.read_text(), 'old-valid-session')
        self.assertEqual(Path(self.owner._email_file).read_text(), 'old@example.test')
        self.assertIs(self.owner._login, self.old)
        self.old.close.assert_not_awaited()
