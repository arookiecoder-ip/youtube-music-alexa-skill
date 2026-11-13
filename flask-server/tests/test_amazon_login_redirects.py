"""Real AlexaPy login must reach MFA after a browser-style POST redirect."""
import asyncio
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock
from network_sessions import ProxyAwareAlexaLogin, _AmazonLoginSession


def response(url, status=200, location=None, html='', method='GET'):
    return SimpleNamespace(url=url, status=status, headers={'Location':location} if location else {},
        history=[], reason='test response', request_info=SimpleNamespace(url=url,method=method,headers={}),
        text=AsyncMock(return_value=html), release=Mock())


class AmazonLoginRedirectTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_alexapy_reaches_mfa_without_reposting_password(self):
        with tempfile.TemporaryDirectory() as directory:
            login=ProxyAwareAlexaLogin('amazon.in','synthetic@example.test','synthetic-password',
                                      lambda name:str(Path(directory)/name))
            actual=login._session
            upstream=SimpleNamespace(get=AsyncMock(),post=AsyncMock(),close=AsyncMock())
            upstream.get.side_effect=[
                response('https://www.amazon.in/ap/signin',html='<form name="signIn" action="/ap/signin"><input name="email"><input name="password"></form>'),
                response('https://www.amazon.in/ap/mfa',html='<form id="auth-mfa-form" action="/ap/mfa"><input id="auth-mfa-otpcode" name="otpCode"></form>')]
            upstream.post.return_value=response('https://www.amazon.in/ap/signin',302,'/ap/mfa',method='POST')
            try:
                login._session=_AmazonLoginSession(upstream,'amazon.in')
                await login.login(data={})
                self.assertTrue(login.status.get('securitycode_required'),login.status)
                self.assertNotIn('login_failed',login.status)
                upstream.post.assert_awaited_once()
                call=upstream.get.await_args
                self.assertEqual(call.args[0],'https://www.amazon.in/ap/mfa')
                self.assertNotIn('data',call.kwargs)
                self.assertNotIn('params',call.kwargs)
                self.assertNotIn('synthetic-password',str(call))
            finally:
                login._session=actual
                await login.close()
                await asyncio.sleep(0)

    async def test_redirect_chain_uses_get_without_credentials_or_authorization(self):
        for code in (301,302,303):
            with self.subTest(code=code):
                upstream=SimpleNamespace(post=AsyncMock(return_value=response('https://www.amazon.in/ap/signin',code,'/ap/mfa')),
                    get=AsyncMock(side_effect=[response('https://www.amazon.in/ap/mfa',302,'/ap/mfa?next=1'),response('https://www.amazon.in/ap/mfa?next=1')]))
                client=_AmazonLoginSession(upstream,'amazon.in')
                self.assertEqual((await client.post('https://www.amazon.in/ap/signin',data={'password':'secret'},
                    headers={'Authorization':'private','Content-Type':'form'},allow_redirects=False)).status,200)
                upstream.post.assert_awaited_once()
                for call in upstream.get.await_args_list:
                    self.assertNotIn('data',call.kwargs)
                    self.assertNotIn('params',call.kwargs)
                    self.assertNotIn('Authorization',call.kwargs['headers'])
                    self.assertFalse(call.kwargs['allow_redirects'])

    async def test_307_and_308_preserve_body_only_within_same_origin(self):
        for code in (307,308):
            upstream=SimpleNamespace(post=AsyncMock(side_effect=[response('https://www.amazon.in/ap/signin',code,'/ap/verify'),response('https://www.amazon.in/ap/verify')]))
            result=await _AmazonLoginSession(upstream,'amazon.in').post('https://www.amazon.in/ap/signin',data={'otp':'synthetic'},allow_redirects=False)
            self.assertEqual(result.status,200)
            self.assertEqual(upstream.post.await_args.kwargs['data'],{'otp':'synthetic'})

    async def test_unsafe_redirects_never_receive_another_request(self):
        for location in ('https://evil.example/ap/mfa','http://www.amazon.in/ap/mfa',
                         'https://www.amazon.in.evil.example/ap/mfa','https://www.amazon.in:8443/ap/mfa',
                         'https://user@www.amazon.in/ap/mfa'):
            with self.subTest(location=location):
                first=response('https://www.amazon.in/ap/signin',302,location)
                upstream=SimpleNamespace(post=AsyncMock(return_value=first),get=AsyncMock())
                with self.assertRaisesRegex(ValueError,'Unsafe Amazon'):
                    await _AmazonLoginSession(upstream,'amazon.in').post('https://www.amazon.in/ap/signin',data={'password':'private'},allow_redirects=False)
                upstream.get.assert_not_awaited();upstream.post.assert_awaited_once();first.release.assert_called_once()

    async def test_cross_origin_307_refuses_to_forward_password(self):
        first=response('https://www.amazon.in/ap/signin',307,'https://www.amazon.com/ap/mfa')
        upstream=SimpleNamespace(post=AsyncMock(return_value=first))
        with self.assertRaisesRegex(ValueError,'Cross-origin'):
            await _AmazonLoginSession(upstream,'amazon.in').post('https://www.amazon.in/ap/signin',data={'password':'private'},allow_redirects=False)
        upstream.post.assert_awaited_once()

    async def test_loop_is_bounded_and_responses_released(self):
        first=response('https://www.amazon.in/ap/signin',302,'/ap/mfa')
        loop=response('https://www.amazon.in/ap/mfa',302,'/ap/mfa')
        upstream=SimpleNamespace(post=AsyncMock(return_value=first),get=AsyncMock(return_value=loop))
        with self.assertRaisesRegex(ValueError,'Too many'):
            await _AmazonLoginSession(upstream,'amazon.in').post('https://www.amazon.in/ap/signin',allow_redirects=False)
        self.assertEqual(upstream.get.await_count,5)
        self.assertEqual(loop.release.call_count,5)

    async def test_intermediate_oauth_callback_is_not_lost_when_the_next_page_redirects(self):
        from network_sessions import ProxyAwareAlexaLogin
        with tempfile.TemporaryDirectory() as directory:
            login=ProxyAwareAlexaLogin('amazon.in','synthetic@example.test','',lambda name:str(Path(directory)/name))
            callback='https://www.amazon.com/ap/maplanding?openid.oa2.authorization_code=synthetic-code'
            upstream=SimpleNamespace(post=AsyncMock(return_value=response('https://www.amazon.in/ap/mfa',302,callback)),
                get=AsyncMock(side_effect=[response(callback,302,'/spa/index.html'),response('https://www.amazon.com/spa/index.html')]))
            try:
                session=_AmazonLoginSession(upstream,'amazon.in',login._capture_oauth_callback)
                await session.post('https://www.amazon.in/ap/mfa',data={'otp':'synthetic'},allow_redirects=False)
                self.assertEqual(login.authorization_code,'synthetic-code')
                upstream.post.assert_awaited_once()
                self.assertEqual(upstream.get.await_count,2)
            finally:
                await login.close();await asyncio.sleep(0)

    async def test_non_signin_requests_keep_original_behavior(self):
        upstream=SimpleNamespace(post=Mock(return_value='original request'))
        client=_AmazonLoginSession(upstream,'amazon.in')
        self.assertEqual(client.post('https://api.amazon.com/auth/register',data={'code':'synthetic'},allow_redirects=False),'original request')
        upstream.post.assert_called_once()
