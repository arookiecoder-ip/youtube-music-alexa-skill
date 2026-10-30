"""Real HTTP/WebSocket transport, without Alexa or production credentials."""
import asyncio
import threading
import unittest
from unittest.mock import patch
from aiohttp import web, WSMsgType
from aiohttp.test_utils import TestClient, TestServer
from playback_events import PlaybackEvents
from realtime_gateway import create_gateway


class GatewayTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.events = PlaybackEvents()
        self.logged_in = True
        self.state = {'queue': [{'video_id': 'song-a', 'entry_id': 'one'}],
                      'queue_version': 1, 'output_token': 'token', 'title': 'Song A'}
        self.snapshot_reads = 0
        async def snapshot(request):
            self.snapshot_reads += 1
            if request.headers.get('Cookie') != 'session=owner' or not self.logged_in:
                raise web.HTTPUnauthorized()
            state = dict(self.state)
            if (request.query.get('queue_version') == str(state['queue_version']) and
                    request.query.get('output_token') == state['output_token']):
                state.pop('queue')
            epoch, revision = self.events.version()
            state.update(event_epoch=epoch, event_revision=revision)
            return web.json_response({'type': 'playback_state', 'event_epoch': epoch,
                                      'event_revision': revision, 'state': state})
        async def audio(request):
            self.assertEqual(request.headers['Range'], 'bytes=2-4')
            self.assertNotEqual(request.headers['X-MusicBox-Client-IP'], 'spoofed')
            response = web.Response(body=b'cde', status=206, headers={'Content-Range': 'bytes 2-4/6'})
            response.headers.add('Set-Cookie', 'one=1')
            response.headers.add('Set-Cookie', 'two=2')
            return response
        upstream = web.Application()
        upstream.router.add_get('/api/app/events/snapshot/', snapshot)
        upstream.router.add_get('/audio/', audio)
        self.upstream = TestServer(upstream)
        await self.upstream.start_server()
        self.client = TestClient(TestServer(create_gateway(str(self.upstream.make_url('')).rstrip('/'),
            self.events, max_connections=2, heartbeat=.15)))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        await self.upstream.close()
        self.assertEqual(len(self.events._listeners), 0)

    async def connect(self):
        return await self.client.ws_connect('/api/app/events/', headers={'Cookie': 'session=owner'})

    async def test_initial_snapshot_immediate_changes_and_queue_deltas(self):
        ws = await self.connect()
        initial = await ws.receive_json(timeout=1)
        self.assertEqual(initial['state']['title'], 'Song A')
        self.state['title'] = 'Song B'
        await asyncio.to_thread(self.events.publish)
        changed = await ws.receive_json(timeout=1)
        self.assertEqual(changed['state']['title'], 'Song B')
        self.assertNotIn('queue', changed['state'])
        self.assertGreater(changed['event_revision'], initial['event_revision'])
        # A transfer needs its full queue even if membership did not change.
        self.state['output_token'] = 'new-owner'
        self.events.publish()
        moved = await ws.receive_json(timeout=1)
        self.assertIn('queue', moved['state'])
        await ws.close()

    async def test_logout_closes_connection_without_another_poll(self):
        ws = await self.connect()
        await ws.receive_json(timeout=1)
        self.logged_in = False
        self.events.publish()
        message = await ws.receive(timeout=1)
        self.assertEqual(message.type, WSMsgType.CLOSE)
        self.assertEqual(message.data, 4001)

    async def test_heartbeat_repairs_missing_notification(self):
        ws = await self.connect()
        await ws.receive_json(timeout=1)
        self.state['title'] = 'Missed event'
        updated = await ws.receive_json(timeout=1)
        self.assertEqual(updated['state']['title'], 'Missed event')
        await ws.close()

    async def test_unauthorized_and_cross_origin_cannot_subscribe(self):
        response = await self.client.get('/api/app/events/')
        self.assertEqual(response.status, 401)
        response = await self.client.get('/api/app/events/', headers={
            'Cookie': 'session=owner', 'Origin': 'https://attacker.example'})
        self.assertEqual(response.status, 403)

    async def test_proxy_cookie_response_cannot_authenticate_another_client(self):
        # A shared proxy pool must never retain cookies received for a caller.
        response = await self.client.get('/audio/', headers={'Range': 'bytes=2-4'})
        await response.read()
        self.client.session.cookie_jar.clear()
        denied = await self.client.get('/api/app/events/')
        self.assertEqual(denied.status, 401)

    async def test_capacity_limit_and_read_only_channel(self):
        first, second = await self.connect(), await self.connect()
        response = await self.client.get('/api/app/events/', headers={'Cookie': 'session=owner'})
        self.assertEqual(response.status, 503)
        self.assertEqual(response.headers['Retry-After'], '5')
        await first.receive_json(timeout=1)
        await first.send_json({'action': 'play'})
        message = await first.receive(timeout=1)
        self.assertEqual(message.type, WSMsgType.CLOSE)
        self.assertEqual(message.data, 1008)
        await second.close()

    async def test_proxy_preserves_ranges_status_and_multiple_cookies(self):
        response = await self.client.get('/audio/', headers={'Range': 'bytes=2-4',
            'X-MusicBox-Client-IP': 'spoofed'})
        self.assertEqual(response.status, 206)
        self.assertEqual(await response.read(), b'cde')
        self.assertEqual(response.headers['Content-Range'], 'bytes 2-4/6')
        self.assertEqual(response.headers.getall('Set-Cookie'), ['one=1', 'two=2'])

    async def test_storm_schedules_only_one_cross_thread_wakeup(self):
        event = self.events.subscribe()
        loop = asyncio.get_running_loop()
        with patch.object(loop, 'call_soon_threadsafe', wraps=loop.call_soon_threadsafe) as scheduled:
            for _ in range(1000):
                self.events.publish()
            self.assertEqual(scheduled.call_count, 1)
        await asyncio.sleep(0)
        self.assertTrue(event.is_set())
        event.clear()
        self.events.publish()
        await asyncio.sleep(0)
        self.assertTrue(event.is_set())
        self.events.unsubscribe(event)

    async def test_notification_storm_is_coalesced_and_restart_changes_epoch(self):
        event = self.events.subscribe()
        thread = threading.Thread(target=lambda: [self.events.publish() for _ in range(1000)])
        thread.start()
        await asyncio.to_thread(thread.join)
        await asyncio.sleep(0)
        self.assertTrue(event.is_set())
        self.assertEqual(self.events.version()[1], 1000)
        self.assertNotEqual(PlaybackEvents().epoch, self.events.epoch)
        self.events.unsubscribe(event)
