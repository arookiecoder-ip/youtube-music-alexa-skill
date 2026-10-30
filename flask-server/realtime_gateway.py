"""Async playback events in front of the existing single-process Flask service.

WebSockets wait on asyncio Events, never on Waitress workers. Regular HTTP,
including range audio, is streamed unchanged to loopback Waitress. Both share
one process and one playback authority; do not start multiple instances without
first moving playback ownership/state into a shared store.
"""
import asyncio
import contextlib
import os
from urllib.parse import urlsplit

import aiohttp
from aiohttp import web
from multidict import CIMultiDict
from playback_events import playback_events

HTTP_CLIENT = web.AppKey('http', aiohttp.ClientSession)
EVENT_CLIENT = web.AppKey('events_http', aiohttp.ClientSession)

HOP_HEADERS = {'connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization',
               'te', 'trailer', 'transfer-encoding', 'upgrade'}


def forwarded_headers(headers):
    connection = {v.strip().lower() for v in headers.get('Connection', '').split(',')}
    return CIMultiDict((k, v) for k, v in headers.items()
                      if k.lower() not in HOP_HEADERS | connection)


def same_origin(origin, host):
    if not origin:  # Native clients use the owner's session cookie.
        return True
    try:
        parsed = urlsplit(origin)
        return parsed.scheme in ('https', 'http') and parsed.netloc.lower() == host.lower()
    except ValueError:
        return False


def create_gateway(upstream, events=playback_events, max_connections=64, heartbeat=15):
    app = web.Application(client_max_size=32 * 1024 * 1024)
    sockets = set()

    async def startup(app):
        # The regular HTTP pool cannot starve the small snapshot/auth pool.
        app[HTTP_CLIENT] = aiohttp.ClientSession(auto_decompress=False, cookie_jar=aiohttp.DummyCookieJar(),
            timeout=aiohttp.ClientTimeout(total=None, connect=10, sock_read=300),
            connector=aiohttp.TCPConnector(limit=128))
        app[EVENT_CLIENT] = aiohttp.ClientSession(auto_decompress=False, cookie_jar=aiohttp.DummyCookieJar(),
            timeout=aiohttp.ClientTimeout(total=5),
            connector=aiohttp.TCPConnector(limit=8))

    async def shutdown(app):
        await asyncio.gather(*(ws.close(code=1001, message=b'server shutdown')
                               for ws in tuple(sockets) if isinstance(ws, web.WebSocketResponse)), return_exceptions=True)

    async def cleanup(app):
        await app[HTTP_CLIENT].close()
        await app[EVENT_CLIENT].close()

    async def snapshot(request, queue_version=None, output_token=None):
        headers = CIMultiDict()
        # No API-key subscription, tokens in URLs, or client-selected upstream.
        if request.headers.get('Cookie'):
            headers['Cookie'] = request.headers['Cookie']
        query = {'serial': request.query.get('serial', 'phone')[:128]}
        if queue_version is not None:
            query['queue_version'] = str(queue_version)
        if output_token is not None:
            query['output_token'] = output_token
        async with app[EVENT_CLIENT].get(upstream + '/api/app/events/snapshot/',
                params=query, headers=headers, allow_redirects=False) as response:
            if response.status != 200:
                raise web.HTTPUnauthorized() if response.status in (301, 302, 401, 403) else web.HTTPServiceUnavailable()
            return await response.json()

    async def websocket(request):
        if not same_origin(request.headers.get('Origin'), request.host):
            raise web.HTTPForbidden()
        if len(sockets) >= max_connections:
            raise web.HTTPServiceUnavailable(headers={'Retry-After': '5'})
        # Reserve capacity before authentication (which suspends).
        reservation = object()
        sockets.add(reservation)
        event = events.subscribe()  # Subscribe before snapshot: no lost transition.
        ws = None
        try:
            initial = await snapshot(request)
            ws = web.WebSocketResponse(heartbeat=heartbeat, max_msg_size=4096)
            sockets.remove(reservation)
            sockets.add(ws)
            await ws.prepare(request)
            queue_version = None
            output_token = None
            async def send(payload):
                nonlocal queue_version, output_token
                state = payload.get('state', {})
                output_token = state.get('output_token')
                if 'queue_version' in state:
                    queue_version = state['queue_version']
                # A slow connection is dropped, never allowed an unbounded backlog.
                await asyncio.wait_for(ws.send_json(payload), timeout=5)
            await send(initial)
            async def updates():
                while not ws.closed:
                    try:
                        await asyncio.wait_for(event.wait(), heartbeat)
                    except asyncio.TimeoutError:
                        pass  # Snapshot also checks revocation and repairs missed events.
                    event.clear()
                    try:
                        payload = await snapshot(request, queue_version, output_token)
                    except web.HTTPUnauthorized:
                        await ws.close(code=4001, message=b'session expired')
                        return
                    try:
                        await send(payload)
                    except (asyncio.TimeoutError, ConnectionError):
                        await ws.close(code=1013, message=b'slow subscriber')
                        return
            async def guarded_updates():
                try:
                    await updates()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    await ws.close(code=1011, message=b'snapshot unavailable')
            task = asyncio.create_task(guarded_updates())
            try:
                async for message in ws:
                    # The event channel is read-only. Commands use authenticated
                    # HTTP routes with existing ownership/token/CSRF checks.
                    if message.type in (aiohttp.WSMsgType.TEXT, aiohttp.WSMsgType.BINARY):
                        await ws.close(code=1008, message=b'read-only stream')
            finally:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            return ws
        finally:
            sockets.discard(reservation)
            if ws is not None:
                sockets.discard(ws)
            events.unsubscribe(event)

    async def proxy(request):
        headers = forwarded_headers(request.headers)
        # This private header is always overwritten; Flask trusts it only from
        # the loopback listener, preserving rate limits per original caller.
        headers['X-MusicBox-Client-IP'] = request.remote or 'unknown'
        try:
            async with app[HTTP_CLIENT].request(request.method, upstream + request.raw_path,
                    headers=headers, data=request.content.iter_any(), allow_redirects=False) as response:
                outgoing = web.StreamResponse(status=response.status, reason=response.reason,
                    headers=forwarded_headers(response.headers))
                await outgoing.prepare(request)
                async for chunk in response.content.iter_chunked(64 * 1024):
                    await outgoing.write(chunk)
                await outgoing.write_eof()
                return outgoing
        except (aiohttp.ClientError, asyncio.TimeoutError):
            raise web.HTTPBadGateway()

    app.router.add_get('/api/app/events/', websocket)
    app.router.add_route('*', '/{path:.*}', proxy)
    app.on_startup.append(startup)
    app.on_shutdown.append(shutdown)
    app.on_cleanup.append(cleanup)
    return app


def main():
    import threading
    import waitress
    import server
    # Internal listener is not exposed to the container network.
    internal = waitress.create_server(server.app, host='127.0.0.1', port=0, threads=16)
    threading.Thread(target=internal.run, name='waitress', daemon=True).start()
    try:
        web.run_app(create_gateway(f'http://127.0.0.1:{internal.effective_port}'),
                    host='0.0.0.0', port=int(os.environ.get('PORT', '5000')),
                    access_log=None)
    finally:
        internal.close()


if __name__ == '__main__':
    main()
