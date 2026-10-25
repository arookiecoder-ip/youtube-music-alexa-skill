import unittest
from unittest.mock import AsyncMock
import httpx
from amazon_login_transport import AmazonLoginTransport

class AmazonTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_transient_page_outage_retries_without_changing_oauth_query(self):
        responses=[httpx.Response(503),httpx.Response(200,content=b'<form>Sign in</form>')]
        upstream=AsyncMock();upstream.handle_async_request.side_effect=responses
        sleep=AsyncMock();transport=AmazonLoginTransport(transport=upstream,sleep=sleep)
        request=httpx.Request('GET','https://www.amazon.com/ap/register?challenge=unchanged')
        response=await transport.handle_async_request(request)
        self.assertEqual(response.status_code,200)
        self.assertEqual(upstream.handle_async_request.await_count,2)
        self.assertIs(upstream.handle_async_request.await_args.args[0],request)
        self.assertTrue(responses[0].is_closed);sleep.assert_awaited_once()
    async def test_credentials_and_verification_posts_are_never_replayed(self):
        upstream=AsyncMock();upstream.handle_async_request.return_value=httpx.Response(503)
        sleep=AsyncMock();transport=AmazonLoginTransport(transport=upstream,sleep=sleep)
        response=await transport.handle_async_request(httpx.Request('POST','https://www.amazon.com/ap/signin',content=b'private-test-data'))
        self.assertEqual(response.status_code,503);upstream.handle_async_request.assert_awaited_once();sleep.assert_not_awaited()
    async def test_retries_are_bounded_and_auth_challenges_are_not_retried(self):
        upstream=AsyncMock();upstream.handle_async_request.side_effect=lambda _:httpx.Response(503)
        transport=AmazonLoginTransport(transport=upstream,sleep=AsyncMock())
        self.assertEqual((await transport.handle_async_request(httpx.Request('GET','https://www.amazon.com/'))).status_code,503)
        self.assertEqual(upstream.handle_async_request.await_count,3)
        upstream.reset_mock();upstream.handle_async_request.side_effect=None;upstream.handle_async_request.return_value=httpx.Response(403)
        self.assertEqual((await transport.handle_async_request(httpx.Request('GET','https://www.amazon.com/'))).status_code,403)
        upstream.handle_async_request.assert_awaited_once()
