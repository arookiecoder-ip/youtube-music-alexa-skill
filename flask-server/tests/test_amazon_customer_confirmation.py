"""Real AlexaPy confirmation binds canonical identity without a second API call."""
import asyncio
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
from network_sessions import ProxyAwareAlexaLogin


def customer_response(customer, url='https://www.amazon.in/api/users/me', status=200):
    return SimpleNamespace(url=url, status=status, history=[], reason='synthetic', headers={},
        request_info=SimpleNamespace(url=url,method='GET',headers={}),
        json=AsyncMock(return_value=customer))


class AmazonCustomerConfirmationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.root=tempfile.TemporaryDirectory()
        self.login=ProxyAwareAlexaLogin('amazon.in','canonical@example.test','synthetic',
            lambda name:str(Path(self.root.name)/name))
        self.customer={'email':'canonical@example.test','id':'amazon-customer'}
        self.patches=[]
        for method in ('get_tokens','register_capabilities','exchange_token_for_cookies','get_csrf','check_domain','save_cookiefile'):
            item=patch.object(self.login,method,AsyncMock(return_value=True));item.start();self.patches.append(item)
        self.get=patch.object(self.login._session,'get',AsyncMock(return_value=customer_response(self.customer,url=self.login._prefix+self.login.url+'/api/users/me')))
        self.request=self.get.start()

    async def asyncTearDown(self):
        self.get.stop()
        for item in reversed(self.patches):item.stop()
        await self.login.close()
        await asyncio.sleep(0)
        self.root.cleanup()

    async def test_email_confirmation_captures_same_customer_response(self):
        self.assertTrue(await self.login.test_loggedin())
        self.assertEqual(self.login.verified_account(),{'authenticated':True,
            'customerEmail':'canonical@example.test','customerId':'amazon-customer'})
        self.request.assert_awaited_once()
        self.assertEqual(self.request.await_args.args[0].split('?')[0],
                         self.login._prefix + self.login.url + '/api/users/me')
        # Callers cannot alter the retained proof by mutating the returned dict.
        profile=self.login.verified_account();profile['customerEmail']='untrusted@example.test'
        self.assertEqual(self.login.verified_account()['customerEmail'],'canonical@example.test')

    async def test_mobile_login_uses_amazon_canonical_email(self):
        self.login.email='+919876543210'
        self.assertTrue(await self.login.test_loggedin())
        self.assertEqual(self.login.verified_account()['customerEmail'],'canonical@example.test')
        self.assertNotEqual(self.login.verified_account()['customerEmail'],self.login.email)

    async def test_marketplace_fallback_captures_only_the_successful_customer(self):
        self.request.side_effect=[customer_response({'email':'','error':'unauthorized'},status=401),
            customer_response(self.customer,url=self.login._prefix+'amazon.com/api/users/me')]
        self.assertTrue(await self.login.test_loggedin())
        self.assertEqual(self.request.await_count,2)
        self.assertEqual(self.login.verified_account()['customerId'],'amazon-customer')
        self.assertEqual(self.login.url,'amazon.com')
        # The real AlexaPy API builder must use the confirmed marketplace too.
        from alexapy import AlexaAPI
        devices=[{'serialNumber':'synthetic-echo','capabilities':['MUSIC_SKILL']}]
        device_response=customer_response({'devices':devices},url='https://alexa.amazon.com/api/devices-v2/device')
        device_response.content_type='application/json'
        self.request.side_effect=None;self.request.return_value=device_response
        self.assertEqual(await AlexaAPI.get_devices(self.login),devices)
        self.assertEqual(str(self.request.await_args.args[0]).split('?')[0],
                         'https://alexa.amazon.com/api/devices-v2/device')
        other=ProxyAwareAlexaLogin('amazon.in','other@example.test','',
                                  lambda name:str(Path(self.root.name)/name))
        try:
            self.assertEqual(other.url,'amazon.in')
            self.assertEqual(self.login.url,'amazon.com')
        finally:
            await other.close()
            await asyncio.sleep(0)

    async def test_untrusted_origin_or_non_success_response_never_becomes_identity(self):
        for url,status in [('https://evil.example/api/users/me',200),
                           ('http://www.amazon.in/api/users/me',200),
                           ('https://www.amazon.in/ap/signin',200),
                           ('https://www.amazon.in/api/users/me',401)]:
            with self.subTest(url=url,status=status):
                self.request.return_value=customer_response(self.customer,url=url,status=status)
                self.assertTrue(await self.login.test_loggedin())
                self.assertIsNone(self.login.verified_account())

    async def test_incomplete_identity_is_denied_even_when_library_accepts_phone_login(self):
        self.login.email='+919876543210'
        for customer in ({}, {'email':'canonical@example.test'}, {'id':'amazon-customer'},
                         {'email':'not-an-email','id':'amazon-customer'}):
            with self.subTest(customer=customer):
                self.request.return_value=customer_response(customer)
                try:
                    await self.login.test_loggedin()
                except (AttributeError, TypeError):
                    # Some AlexaPy versions reject incomplete customers by
                    # raising while formatting their own diagnostic message.
                    pass
                self.assertIsNone(self.login.verified_account())

    async def test_invalidated_or_closed_session_cannot_reuse_confirmed_identity(self):
        await self.login.test_loggedin()
        self.login.status['login_successful']=False
        self.assertIsNone(self.login.verified_account())
        self.login.status['login_successful']=True
        self.login.customer_id='different-customer'
        self.assertIsNone(self.login.verified_account())
        self.login.customer_id='amazon-customer'
        self.assertIsNotNone(self.login.verified_account())
        real=self.login._session._session
        await self.login.close()
        self.assertIsNone(real._connector)
        self.assertIsNone(self.login.verified_account())

    async def test_no_identity_is_available_before_confirmation(self):
        self.login.status={'login_successful':True}
        self.login.customer_id='amazon-customer'
        self.assertIsNone(self.login.verified_account())


class AmazonOtpPromotionTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_otp_flow_confirms_identity_saves_session_and_removes_staging(self):
        import ast, os, shutil, time
        from unittest.mock import Mock
        source=Path(__file__).resolve().parents[1]/'alexa_remote.py'
        cls=next(n for n in ast.parse(source.read_text()).body if isinstance(n,ast.ClassDef) and n.name=='AlexaRemote')
        cls.body=[n for n in cls.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))
                  and n.name in {'_form_step','_form_state','_form_cleanup','_promote_staged_login','_verified_account'}]
        namespace={'os':os,'shutil':shutil,'time':time,'tempfile':tempfile,'asyncio':asyncio,
                   'OWNER':'owner','logger':Mock(),'_renew_proxy_claim':Mock(),'_release_proxy_claim':Mock()}
        exec(compile(ast.Module(body=[cls],type_ignores=[]),str(source),'exec'),namespace)
        with tempfile.TemporaryDirectory() as root:
            root=Path(root);stage=root/'pending';(stage/'.storage').mkdir(parents=True)
            live=root/'live';(live/'.storage').mkdir(parents=True)
            login=ProxyAwareAlexaLogin('amazon.in','canonical@example.test','synthetic-password',lambda name:str(stage/name))
            session=login._session._session
            def page(url,html='',status=200,location=None,customer=None):
                return SimpleNamespace(url=url,status=status,history=[],reason='synthetic',
                    headers={'Location':location} if location else {},
                    request_info=SimpleNamespace(url=url,method='GET',headers={}),
                    text=AsyncMock(return_value=html),json=AsyncMock(return_value=customer),release=Mock())
            get=AsyncMock(side_effect=[
                page('https://www.amazon.in/ap/signin', '<form name="signIn" action="/ap/signin"><input name="email"><input name="password"></form>'),
                page('https://www.amazon.in/ap/mfa', '<form id="auth-mfa-form" action="/ap/mfa"><input id="auth-mfa-otpcode" name="otpCode"></form>'),
                page(login._prefix+'amazon.in/api/users/me',status=401,customer={'email':'','error':'unauthorized'}),
                page(login._prefix+'amazon.com/api/users/me',customer={'email':'canonical@example.test','id':'amazon-customer'})])
            post=AsyncMock(side_effect=[page('https://www.amazon.in/ap/signin',status=302,location='/ap/mfa'),
                                       page('https://www.amazon.in/ap/maplanding')])
            remote=namespace['AlexaRemote']();old=SimpleNamespace(close=AsyncMock())
            for name,value in dict(_form_login=login,_form_stage=str(stage),_form_attempt_id='synthetic-attempt',
                FORM_STEP_TIMEOUT=5,_login=old,_proxy_login=None,_identity_sink=Mock(),
                _musicbox_identity='user:explicit-user',_access_error=Mock(return_value=None),
                _read_saved_email=Mock(return_value=None),cookie_dir=str(live),_storage_dir=str(live/'.storage'),
                _email_file=str(live/'.storage/last_email.txt')).items():setattr(remote,name,value)
            patches=[patch.object(session,'get',get),patch.object(session,'post',post)]
            for method in ('get_tokens','register_capabilities','exchange_token_for_cookies','get_csrf','check_domain'):
                patches.append(patch.object(login,method,AsyncMock(return_value=True)))
            for item in patches:item.start()
            try:
                first=await remote._form_step({})
                self.assertEqual(first['state'],'need_code')
                result=await remote._form_step({'securitycode':'123456'})
                self.assertEqual(result['state'],'connected')
                self.assertEqual(get.await_count,4)
                self.assertIn('amazon.com/api/users/me',get.await_args.args[0])
                self.assertEqual(login.url,'amazon.com')
                remote._identity_sink.assert_called_once_with('user:explicit-user','canonical@example.test','amazon-customer')
                self.assertIs(remote._login,login)
                self.assertTrue(Path(login._cookiefile[0]).is_file())
                self.assertTrue(Path(login._cookiefile[0]).is_relative_to(live))
                self.assertEqual(Path(remote._email_file).read_text(),'canonical@example.test')
                self.assertFalse(stage.exists())
                self.assertFalse(session.closed)
                old.close.assert_awaited_once()
            finally:
                for item in reversed(patches):item.stop()
                await login.close()
                await asyncio.sleep(0)
