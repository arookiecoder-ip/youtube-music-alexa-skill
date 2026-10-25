import asyncio
import importlib.util
from pathlib import Path
import unittest

path = Path(__file__).resolve().parents[2] / 'network-egress' / 'proxy.py'
spec = importlib.util.spec_from_file_location('egress_proxy', path)
proxy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(proxy)


class EgressTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.servers = []
        self.connections = []
        async def echo(reader, writer):
            self.connections.append(True)
            try:
                data = await reader.read(65536)
                writer.write(b'reply:' + data)
                await writer.drain()
            finally:
                writer.close()
        self.target = await self.serve(echo)

    async def serve(self, handler):
        server = await asyncio.start_server(handler, '127.0.0.1', 0)
        self.servers.append(server)
        return server.sockets[0].getsockname()[1]

    async def asyncTearDown(self):
        for server in self.servers:
            server.close()
            await server.wait_closed()
        await asyncio.sleep(.01)

    async def dial_target(self, host, port):
        return await asyncio.open_connection('127.0.0.1', self.target)

    async def exchange(self, gateway, request=b'CONNECT example.test:443 HTTP/1.1\r\nHost: example.test\r\n\r\n'):
        port = await self.serve(gateway.handle)
        reader, writer = await asyncio.open_connection('127.0.0.1', port)
        writer.write(request)
        await writer.drain()
        header = await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'), 1)
        if b' 200 ' in header:
            writer.write(b'opaque TLS bytes')
            await writer.drain()
            body = await asyncio.wait_for(reader.read(), 1)
        else:
            body = b''
        writer.close()
        await writer.wait_closed()
        return header, body

    async def test_prefers_vpn_and_preserves_end_to_end_bytes(self):
        vpn = proxy.Gateway(required_token='secret', dial=self.dial_target)
        vpn_port = await self.serve(vpn.handle)
        gateway = proxy.Gateway(upstream=f'127.0.0.1:{vpn_port}', token='secret')
        header, body = await self.exchange(gateway)
        self.assertIn(b' 200 ', header)
        self.assertEqual(body, b'reply:opaque TLS bytes')
        self.assertEqual(gateway.route, 'vpn')
        self.assertEqual(len(self.connections), 1)

    async def test_missing_vpn_falls_back_and_recovers_after_cooldown(self):
        calls = []
        available = False
        async def dial(host, port):
            calls.append(host)
            if host == 'vpn.test':
                if not available:
                    raise OSError('unavailable')
                return await asyncio.open_connection('127.0.0.1', vpn_port)
            return await self.dial_target(host, port)
        vpn = proxy.Gateway(required_token='secret', dial=self.dial_target)
        vpn_port = await self.serve(vpn.handle)
        gateway = proxy.Gateway(upstream='vpn.test:5000', token='secret', dial=dial)
        self.assertEqual((await self.exchange(gateway))[1], b'reply:opaque TLS bytes')
        self.assertEqual(gateway.route, 'direct')
        await self.exchange(gateway)
        self.assertEqual(calls.count('vpn.test'), 1)
        available = True
        gateway.retry_at = 0
        await self.exchange(gateway)
        self.assertEqual(gateway.route, 'vpn')

    async def test_hung_vpn_connection_has_bounded_fallback(self):
        async def dial(host, port):
            if host == 'vpn.test':
                await asyncio.sleep(10)
            return await self.dial_target(host, port)
        gateway = proxy.Gateway(upstream='vpn.test:5000', dial=dial, timeout=.03)
        self.assertEqual((await asyncio.wait_for(self.exchange(gateway), .5))[1], b'reply:opaque TLS bytes')
        self.assertEqual(gateway.route, 'direct')

    async def test_vpn_endpoint_rejects_missing_token_without_dialing(self):
        gateway = proxy.Gateway(required_token='secret', dial=self.dial_target)
        self.assertIn(b' 407 ', (await self.exchange(gateway))[0])
        self.assertEqual(len(self.connections), 0)

    async def test_unauthorized_vpn_response_uses_direct_connection(self):
        vpn = proxy.Gateway(required_token='correct', dial=self.dial_target)
        port = await self.serve(vpn.handle)
        async def dial(host, target_port):
            if host == 'vpn.test':
                return await asyncio.open_connection('127.0.0.1', port)
            return await self.dial_target(host, target_port)
        gateway = proxy.Gateway(upstream='vpn.test:5000', token='wrong', dial=dial)
        self.assertEqual((await self.exchange(gateway))[1], b'reply:opaque TLS bytes')
        self.assertEqual(gateway.route, 'direct')
        self.assertEqual(len(self.connections), 1)

    async def test_invalid_ports_are_rejected_before_outbound_connection(self):
        gateway = proxy.Gateway(dial=self.dial_target)
        self.assertIn(b' 502 ', (await self.exchange(gateway, b'CONNECT example.test:22 HTTP/1.1\r\n\r\n'))[0])
        self.assertEqual(len(self.connections), 0)

class VpnLifecycleTests(unittest.TestCase):
    def test_restart_recreation_and_missing_vpn_keep_app_independent(self):
        path = Path(__file__).resolve().parents[2] / 'scripts' / 'ensure-vpn-proxy.py'
        spec = importlib.util.spec_from_file_location('vpn_lifecycle', path)
        lifecycle = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(lifecycle)
        vpn = {'Id':'new', 'State':{'Running':True, 'StartedAt':'2026-10-25T02:00:00Z'}}
        endpoint = {'State':{'Running':True,'StartedAt':'2026-10-25T01:00:00Z'},'HostConfig':{'NetworkMode':'container:new'}}
        self.assertFalse(lifecycle.needs_recreate(None, endpoint))
        self.assertTrue(lifecycle.needs_recreate(vpn, None))
        self.assertTrue(lifecycle.needs_recreate(vpn, endpoint))
        endpoint['State']['StartedAt']='2026-10-25T03:00:00Z'
        self.assertFalse(lifecycle.needs_recreate(vpn, endpoint))
        endpoint['HostConfig']['NetworkMode']='container:old'
        self.assertTrue(lifecycle.needs_recreate(vpn, endpoint))
