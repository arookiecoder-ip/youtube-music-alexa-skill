"""Run only in the isolated CI container, with its disposable owner account."""
import asyncio
import os
import aiohttp


async def main():
    root = 'http://127.0.0.1:5000'
    async with aiohttp.ClientSession(cookie_jar=aiohttp.DummyCookieJar(),
            headers={'Accept': 'application/json'}, timeout=aiohttp.ClientTimeout(total=10)) as client:
        async with client.post(root + '/login/', json={
                'username': os.environ['REMOTE_USER'], 'password': os.environ['REMOTE_PASSWORD']}) as response:
            assert response.status == 200, f'CI login failed: {response.status}'
            # This loopback-only smoke test sends the signed Secure cookie
            # explicitly; production clients use TLS and their regular jar.
            cookie = response.cookies['session'].value
            assert not (await response.json()).get('totp_required')
        headers = {'Cookie': 'session=' + cookie}
        async with client.ws_connect(root + '/api/app/events/?serial=phone', headers=headers) as ws:
            state = await ws.receive_json(timeout=5)
            assert state['type'] == 'playback_state'
            assert state['event_epoch'] and state['state']['output_token']
            assert isinstance(state['state']['queue'], list)
            async with client.post(root + '/logout/', json={}, headers=headers) as response:
                assert response.status == 200
            message = await ws.receive(timeout=5)
            assert message.type == aiohttp.WSMsgType.CLOSE, message.type
            assert message.data == 4001, message.data
        async with client.get(root + '/api/app/events/snapshot/', headers=headers) as response:
            assert response.status == 401, 'Revoked signed cookie still authorized a snapshot'
    print('Real owner login, WebSocket snapshot, logout and cookie revocation passed')


if __name__ == '__main__':
    asyncio.run(main())
