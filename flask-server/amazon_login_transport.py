"""Retry only idempotent Amazon sign-in page loads on transient outages."""
import asyncio
import httpx

class AmazonLoginTransport(httpx.AsyncBaseTransport):
    def __init__(self, proxy=None, transport=None, sleep=asyncio.sleep):
        self.transport = transport or httpx.AsyncHTTPTransport(retries=3,http2=True,proxy=proxy)
        self.sleep=sleep
    async def handle_async_request(self, request):
        for attempt in range(3):
            response=await self.transport.handle_async_request(request)
            if request.method != 'GET' or response.status_code not in (500,502,503,504) or attempt == 2:
                return response
            await response.aclose()
            await self.sleep(.3*(attempt+1))
    async def aclose(self):
        await self.transport.aclose()
