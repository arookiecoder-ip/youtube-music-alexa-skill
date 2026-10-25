"""Private HTTP CONNECT gateway: prefer VPN, fail open before sending request data.

TLS remains end to end. Established tunnels are never replayed on another
connection; callers recover interrupted media with their existing Range retries.
"""
import asyncio
from contextlib import suppress
import hmac
import json
import logging
import os
import time
from urllib.parse import urlsplit

log = logging.getLogger('musicbox-egress')


class Gateway:
    def __init__(self, upstream='', token='', required_token='', timeout=1.5, cooldown=10, dial=asyncio.open_connection):
        self.upstream = upstream
        self.token = token
        self.required_token = required_token
        self.timeout = timeout
        self.cooldown = cooldown
        self.dial = dial
        self.retry_at = 0
        self.route = 'unknown'
        self.slots = asyncio.Semaphore(128)

    async def connect(self, host, port):
        if self.upstream and time.monotonic() >= self.retry_at:
            remote = None
            try:
                async with asyncio.timeout(self.timeout):
                    upstream = urlsplit('http://' + self.upstream)
                    reader, remote = await self.dial(upstream.hostname, upstream.port or 5000)
                    authority = f'[{host}]:{port}' if ':' in host else f'{host}:{port}'
                    remote.write((f'CONNECT {authority} HTTP/1.1\r\nHost: {authority}\r\nProxy-Authorization: Bearer {self.token}\r\n\r\n').encode())
                    await remote.drain()
                    response = await reader.readuntil(b'\r\n\r\n')
                    if response.split(b' ', 2)[1] != b'200':
                        raise OSError('VPN connection unavailable')
                self.route = 'vpn'
                return reader, remote
            except (OSError, TimeoutError, ValueError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
                if remote:
                    remote.close()
                    with suppress(OSError):
                        await remote.wait_closed()
                self.retry_at = time.monotonic() + self.cooldown
                if self.route != 'direct':
                    log.warning('VPN unavailable; using direct network (retry in %ss)', self.cooldown)
        # No target HTTP body has been sent yet, so this cannot duplicate an
        # Amazon command or upload when the preferred connection fails.
        async with asyncio.timeout(self.timeout):
            result = await self.dial(host, port)
        self.route = 'direct'
        return result

    async def handle(self, reader, writer):
        remote = None
        responded = False
        try:
            async with self.slots:
                async with asyncio.timeout(10):
                    block = await reader.readuntil(b'\r\n\r\n')
                lines = block.decode('iso-8859-1').split('\r\n')
                method, target, protocol = lines[0].split(' ')
                if protocol not in ('HTTP/1.0', 'HTTP/1.1'):
                    raise ValueError('Invalid protocol')
                headers = [line.split(':', 1) for line in lines[1:] if line]
                if any(len(pair) != 2 for pair in headers):
                    raise ValueError('Invalid headers')
                values = {name.lower(): value.strip() for name, value in headers}
                if target == '/health' and not self.required_token:
                    body = json.dumps({'ready': True, 'route': self.route,
                        'vpn_retry_ms': max(0, int((self.retry_at - time.monotonic()) * 1000))}).encode()
                    writer.write(b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\nContent-Length: ' + str(len(body)).encode() + b'\r\n\r\n' + body)
                    await writer.drain()
                    return
                if self.required_token and not hmac.compare_digest(values.get('proxy-authorization', ''), 'Bearer ' + self.required_token):
                    writer.write(b'HTTP/1.1 407 Proxy Authentication Required\r\nContent-Length: 0\r\nConnection: close\r\n\r\n')
                    await writer.drain()
                    return
                if method == 'CONNECT':
                    url = urlsplit('//' + target)
                    host, port = url.hostname, url.port
                    initial = b''
                else:
                    url = urlsplit(target)
                    if url.scheme != 'http' or url.username or url.password or url.fragment:
                        raise ValueError('Expected absolute HTTP URL')
                    host, port = url.hostname, url.port or 80
                    path = (url.path or '/') + ('?' + url.query if url.query else '')
                    cleaned = [(k, v) for k, v in headers if k.lower() not in ('proxy-authorization', 'proxy-connection', 'connection')]
                    initial = (f'{method} {path} {protocol}\r\n' + ''.join(f'{k}:{v}\r\n' for k, v in cleaned) + 'Connection: close\r\n\r\n').encode('iso-8859-1')
                if not host or port not in (80, 443):
                    raise ValueError('Unsupported destination')
                upstream_reader, remote = await self.connect(host, port)
                if method == 'CONNECT':
                    writer.write(b'HTTP/1.1 200 Connection Established\r\n\r\n')
                    await writer.drain()
                    responded = True
                else:
                    remote.write(initial)
                    await remote.drain()
                    responded = True
                async def pipe(source, destination):
                    while True:
                        async with asyncio.timeout(300):
                            chunk = await source.read(65536)
                        if not chunk:
                            if destination.can_write_eof():
                                destination.write_eof()
                            return
                        destination.write(chunk)
                        await destination.drain()
                jobs = [asyncio.create_task(pipe(reader, remote)), asyncio.create_task(pipe(upstream_reader, writer))]
                try:
                    done, pending = await asyncio.wait(jobs, return_when=asyncio.FIRST_COMPLETED)
                    for job in done:
                        job.result()
                finally:
                    for job in jobs:
                        job.cancel()
                    await asyncio.gather(*jobs, return_exceptions=True)
        except (OSError, TimeoutError, ValueError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
            if not responded:
                writer.write(b'HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\nConnection: close\r\n\r\n')
                try:
                    await writer.drain()
                except OSError:
                    pass
        finally:
            if remote:
                remote.close()
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass


async def main():
    token = os.environ.get('MUSICBOX_EGRESS_TOKEN', '')
    vpn_mode = os.environ.get('EGRESS_MODE') == 'vpn'
    if not token:
        raise SystemExit('MUSICBOX_EGRESS_TOKEN is required')
    gateway = Gateway(upstream='' if vpn_mode else os.environ.get('VPN_UPSTREAM', 'gluetun:5000'),
        token=token, required_token=token if vpn_mode else '',
        timeout=float(os.environ.get('EGRESS_CONNECT_TIMEOUT', '1.5')),
        cooldown=float(os.environ.get('EGRESS_VPN_RETRY_SECONDS', '10')))
    bind = os.environ.get('EGRESS_BIND', '0.0.0.0')
    server = await asyncio.start_server(gateway.handle, bind, int(os.environ.get('EGRESS_PORT', '8890')), limit=32768)
    async with server:
        await server.serve_forever()


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
    asyncio.run(main())
