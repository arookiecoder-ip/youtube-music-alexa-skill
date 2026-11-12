"""Disk-free relay regression cases, including failure recovery and cleanup."""
import io
import threading
import os
from pathlib import Path
import sys
import tempfile
import concurrent.futures
import unittest
from unittest.mock import Mock
from urllib.error import HTTPError
from youtube_stream import YouTubeStream, StreamError, valid_media_url, _MediaRedirect

VID = 'dQw4w9WgXcQ'
MEDIA = {'url':'https://rr1.googlevideo.com/audio', 'itag':140,'length':12,'modified':'1'}


class Upstream(io.BytesIO):
    def __init__(self, body=b'123456789012', status=200, headers=None):
        super().__init__(body)
        self.status = status
        self.headers = {'Content-Type':'audio/mp4', 'Content-Length':str(len(body)), **(headers or {})}
    read1 = io.BytesIO.read


class Streaming(unittest.TestCase):
    def setUp(self):
        self.opener = Mock()
        self.stream = YouTubeStream(lambda:'',opener=self.opener, capacity=1)
        self.stream.resolve = Mock(return_value=MEDIA)
        self.addCleanup(self.stream.close)

    def test_public_metadata_never_exposes_the_signed_stream_url(self):
        self.stream.resolve.return_value = {**MEDIA, 'metadata': {'title':'Song', 'duration_ms':123000, 'url':MEDIA['url'], 'cookies':'private'}}
        result = self.stream.metadata(VID)
        self.assertEqual(result['title'], 'Song')
        self.assertEqual(result['duration_ms'], 123000)
        self.assertNotIn('url', result)
        self.assertNotIn('cookies', result)

    def test_first_bytes_and_close_uniterated_release_socket_and_slot(self):
        up=Upstream();self.opener.open.return_value=up
        relay=self.stream.open(VID)
        self.assertEqual(relay.first,b'123456789012')
        relay.close();relay.close();self.assertTrue(up.closed)
        self.assertTrue(self.stream.slots.acquire(False));self.stream.slots.release()

    def test_byte_range_forwarded_and_status_preserved(self):
        self.opener.open.return_value=Upstream(b'5678901',status=206,headers={'Content-Range':'bytes 5-11/12'})
        relay=self.stream.open(VID,byte_range='bytes=5-11')
        self.assertEqual(self.opener.open.call_args.args[0].get_header('Range'),'bytes=5-11')
        self.assertEqual(relay.status,206);list(relay.chunks())

    def test_suffix_range_is_normalized_for_youtube(self):
        self.opener.open.return_value=Upstream(b'8901',status=206,headers={'Content-Range':'bytes 8-11/12'})
        relay=self.stream.open(VID,byte_range='bytes=-4')
        self.assertEqual(self.opener.open.call_args.args[0].get_header('Range'),'bytes=8-11')
        relay.close()

    def test_unsatisfiable_range_rejected_with_total_length(self):
        with self.assertRaises(StreamError) as error:self.stream.open(VID,byte_range='bytes=12-')
        self.assertEqual(error.exception.status,416)
        self.opener.open.assert_not_called()

    def test_wrong_content_range_is_not_delivered_as_valid_audio(self):
        self.opener.open.return_value=Upstream(b'123',status=206,headers={'Content-Range':'bytes 0-2/12'})
        with self.assertRaises(StreamError):self.stream.open(VID,byte_range='bytes=5-7')

    def test_head_never_reads_audio(self):
        up=Upstream();up.read1=Mock(side_effect=AssertionError('HEAD read body'))
        self.opener.open.return_value=up
        relay=self.stream.open(VID,method='HEAD');self.assertEqual(relay.first,b'');relay.close()

    def test_expired_url_is_refreshed_once_before_any_audio(self):
        stale=HTTPError(MEDIA['url'],403,'private',{},io.BytesIO())
        self.opener.open.side_effect=[stale,Upstream()]
        relay=self.stream.open(VID)
        self.assertEqual(self.stream.resolve.call_args.kwargs,{'refresh':True});relay.close()

    def test_auth_error_after_refresh_does_not_loop_or_leak_slot(self):
        self.opener.open.side_effect=lambda *a,**k: (_ for _ in ()).throw(HTTPError(MEDIA['url'],403,'private',{},io.BytesIO()))
        with self.assertRaises(StreamError):self.stream.open(VID)
        self.assertEqual(self.opener.open.call_count,2)
        self.assertTrue(self.stream.slots.acquire(False));self.stream.slots.release()

    def test_rate_limit_has_retryable_status(self):
        self.opener.open.side_effect=HTTPError(MEDIA['url'],429,'private',{},io.BytesIO())
        with self.assertRaises(StreamError) as e:self.stream.open(VID)
        self.assertEqual(e.exception.status,503);self.assertEqual(self.opener.open.call_count,1)

    def test_range_ignored_html_and_empty_audio_are_not_success(self):
        for up,kw in [(Upstream(),{'byte_range':'bytes=0-1'}),
                      (Upstream(headers={'Content-Type':'text/html'}),{}),(Upstream(b''),{})]:
            self.opener.open.return_value=up
            with self.assertRaises(StreamError):self.stream.open(VID,**kw)
            self.assertTrue(up.closed)

    def test_malformed_ranges_are_rejected_before_resolution(self):
        for value in ['bytes=0-1,3-4','invalid','bytes=--1']:
            with self.assertRaises(StreamError) as e:self.stream.open(VID,byte_range=value)
            self.assertEqual(e.exception.status,416)
        self.stream.resolve.assert_not_called()

    def test_if_range_mismatch_serves_complete_representation(self):
        self.opener.open.return_value=Upstream()
        relay=self.stream.open(VID,byte_range='bytes=3-',if_range='"old"')
        self.assertEqual(self.opener.open.call_args.args[0].get_header('Range'), 'bytes=0-11')
        self.assertEqual(relay.status, 200)
        self.assertNotIn('Content-Range', relay.headers)
        relay.close()

    def test_capacity_is_bounded_and_recovers_after_disconnect(self):
        self.opener.open.return_value=Upstream()
        relay=self.stream.open(VID)
        with self.assertRaises(StreamError) as e:self.stream.open(VID)
        self.assertEqual(e.exception.code,'busy');relay.close()
        self.opener.open.return_value=Upstream();self.stream.open(VID).close()

    def test_read_failure_releases_slot(self):
        up=Upstream(b'x'*20000)
        self.stream.resolve.return_value = {**MEDIA, 'length': 20000}
        self.opener.open.side_effect = [up, OSError('offline'), OSError('offline')]
        relay=self.stream.open(VID)
        up.read1=Mock(side_effect=OSError('broken connection'))
        with self.assertRaises(StreamError) as error:list(relay.chunks())
        self.assertEqual(error.exception.code, 'upstream_interrupted')
        self.assertEqual(self.opener.open.call_count, 3)
        self.assertTrue(up.closed);self.assertTrue(self.stream.slots.acquire(False));self.stream.slots.release()

    def test_early_eof_resumes_exactly_without_duplicate_bytes(self):
        first = Upstream(b'1234', headers={'Content-Length': '12'})
        rest = Upstream(b'56789012', status=206,
            headers={'Content-Range': 'bytes 4-11/12'})
        self.opener.open.side_effect = [first, rest]
        self.stream.current.activate(VID)
        relay = self.stream.open(VID)
        blocks = []
        for block in relay.chunks():
            relay.remember(block)
            blocks.append(block)
        self.assertEqual(b''.join(blocks), b'123456789012')
        cached = self.stream.current.open(VID)
        self.assertEqual(cached.read1(12), b'123456789012')
        cached.close()
        self.assertEqual(self.opener.open.call_args.args[0].get_header('Range'), 'bytes=4-11')
        self.assertTrue(self.stream.resolve.call_args.kwargs['refresh'])
        self.assertEqual(relay.headers['Content-Length'], '12')
        self.assertTrue(first.closed and rest.closed)
        self.assertEqual(self.stream.snapshot()['active_streams'], 0)

    def test_failed_reconnect_closes_http_error_and_remains_bounded(self):
        first = Upstream(b'1234', headers={'Content-Length': '12'})
        error_body = io.BytesIO(b'private provider response')
        refused = HTTPError(MEDIA['url'], 403, 'private', {}, error_body)
        rest = Upstream(b'56789012', status=206, headers={'Content-Range': 'bytes 4-11/12'})
        self.opener.open.side_effect = [first, refused, rest]
        relay = self.stream.open(VID)
        self.assertEqual(b''.join(relay.chunks()), b'123456789012')
        self.assertTrue(error_body.closed)
        self.assertEqual(self.opener.open.call_count, 3)

    def test_recovery_preserves_original_client_range(self):
        first = Upstream(b'3456', status=206,
            headers={'Content-Length': '8', 'Content-Range': 'bytes 2-9/12'})
        rest = Upstream(b'7890', status=206,
            headers={'Content-Range': 'bytes 6-9/12'})
        self.opener.open.side_effect = [first, rest]
        relay = self.stream.open(VID, byte_range='bytes=2-9')
        self.assertEqual(b''.join(relay.chunks()), b'34567890')
        self.assertEqual(self.opener.open.call_args.args[0].get_header('Range'), 'bytes=6-9')
        self.assertEqual(relay.headers['Content-Range'], 'bytes 2-9/12')

    def test_partial_exception_bytes_are_not_lost_or_repeated(self):
        import http.client
        first = Upstream(headers={'Content-Length': '12'})
        first.read1 = Mock(side_effect=[b'1234', http.client.IncompleteRead(b'56', 6), b''])
        rest = Upstream(b'789012', status=206, headers={'Content-Range': 'bytes 6-11/12'})
        self.opener.open.side_effect = [first, rest]
        relay = self.stream.open(VID)
        self.assertEqual(b''.join(relay.chunks()), b'123456789012')
        self.assertEqual(self.opener.open.call_args.args[0].get_header('Range'), 'bytes=6-11')

    def test_recovery_budget_is_shared_across_interruptions(self):
        self.opener.open.side_effect = [
            Upstream(b'1234', headers={'Content-Length': '12'}),
            Upstream(b'5678', status=206, headers={'Content-Length': '8', 'Content-Range': 'bytes 4-11/12'}),
            Upstream(b'9012', status=206, headers={'Content-Range': 'bytes 8-11/12'})]
        self.assertEqual(b''.join(self.stream.open(VID).chunks()), b'123456789012')
        self.assertEqual(self.opener.open.call_count, 3)

    def test_recovery_rejects_changed_representation(self):
        self.opener.open.return_value = Upstream(b'1234', headers={'Content-Length': '12'})
        self.stream.resolve.side_effect = [MEDIA, {**MEDIA, 'itag': 141}]
        relay = self.stream.open(VID)
        with self.assertRaises(StreamError) as error:
            list(relay.chunks())
        self.assertEqual(error.exception.code, 'audio_representation_changed')
        self.assertEqual(self.opener.open.call_count, 1)
        self.assertEqual(self.stream.snapshot()['active_streams'], 0)

    def test_recovery_rejects_ignored_range_and_closes_socket(self):
        first, wrong = Upstream(b'1234', headers={'Content-Length': '12'}), Upstream()
        self.opener.open.side_effect = [first, wrong]
        relay = self.stream.open(VID)
        with self.assertRaises(StreamError) as error:
            list(relay.chunks())
        self.assertEqual(error.exception.code, 'invalid_recovery_range')
        self.assertTrue(wrong.closed)
        self.assertEqual(self.stream.snapshot()['active_streams'], 0)

    def test_client_close_does_not_start_recovery(self):
        self.opener.open.return_value = Upstream(b'1234', headers={'Content-Length': '12'})
        relay = self.stream.open(VID)
        relay.close()
        self.assertEqual(self.opener.open.call_count, 1)

    def test_wrong_initial_length_cannot_be_served_as_success(self):
        self.opener.open.return_value = Upstream(b'1234')
        with self.assertRaises(StreamError) as error:
            self.stream.open(VID)
        self.assertEqual(error.exception.code, 'invalid_audio_length')

    def test_full_audio_uses_bounded_ranges_without_spending_recovery_budget(self):
        body = b'abcdefghijklmnop'
        self.stream.resolve.return_value = {**MEDIA, 'length': len(body)}
        self.stream.UPSTREAM_CHUNK_BYTES = 4
        self.opener.open.side_effect = [Upstream(body[i:i+4], status=206,
            headers={'Content-Range': f'bytes {i}-{i+3}/16'}) for i in range(0, 16, 4)]
        self.stream.current.activate(VID)
        relay = self.stream.open(VID)
        blocks = []
        for block in relay.chunks():
            relay.remember(block)
            blocks.append(block)
        self.assertEqual(b''.join(blocks), body)
        self.assertEqual(relay.status, 200)
        self.assertEqual(relay.headers['Content-Length'], '16')
        self.assertNotIn('Content-Range', relay.headers)
        self.assertEqual(relay.upstream.attempts, 0)
        self.assertEqual(self.stream.resolve.call_count, 1)
        self.assertEqual([c.args[0].get_header('Range') for c in self.opener.open.call_args_list],
                         ['bytes=0-3', 'bytes=4-7', 'bytes=8-11', 'bytes=12-15'])
        cached = self.stream.current.open(VID)
        self.assertEqual(cached.read1(16), body)
        cached.close()
        self.assertEqual(self.stream.snapshot()['active_streams'], 0)

    def test_partial_client_response_preserves_its_range_across_segments(self):
        body = b'abcdefghijklmnop'
        self.stream.resolve.return_value = {**MEDIA, 'length': len(body)}
        self.stream.UPSTREAM_CHUNK_BYTES = 4
        self.opener.open.side_effect = [Upstream(body[i:i+4], status=206,
            headers={'Content-Range': f'bytes {i}-{i+3}/16'}) for i in (2, 6, 10)]
        relay = self.stream.open(VID, byte_range='bytes=2-13')
        self.assertEqual(b''.join(relay.chunks()), body[2:14])
        self.assertEqual(relay.status, 206)
        self.assertEqual(relay.headers['Content-Length'], '12')
        self.assertEqual(relay.headers['Content-Range'], 'bytes 2-13/16')
        self.assertEqual(self.stream.resolve.call_count, 1)

    def test_failure_inside_segment_recovers_and_normal_segments_continue(self):
        self.stream.resolve.return_value = {**MEDIA, 'length': 16}
        self.stream.UPSTREAM_CHUNK_BYTES = 4
        self.opener.open.side_effect = [
            Upstream(b'ab', status=206, headers={'Content-Length':'4','Content-Range':'bytes 0-3/16'}),
            Upstream(b'cdef', status=206, headers={'Content-Range':'bytes 2-5/16'}),
            Upstream(b'ghij', status=206, headers={'Content-Range':'bytes 6-9/16'}),
            Upstream(b'klmn', status=206, headers={'Content-Range':'bytes 10-13/16'}),
            Upstream(b'op', status=206, headers={'Content-Range':'bytes 14-15/16'})]
        relay = self.stream.open(VID)
        self.assertEqual(b''.join(relay.chunks()), b'abcdefghijklmnop')
        self.assertEqual(relay.upstream.attempts, 1)
        self.assertEqual(self.stream.resolve.call_count, 2)
        self.assertEqual(self.opener.open.call_count, 5)

    def test_expired_url_at_normal_segment_boundary_refreshes_once(self):
        self.stream.UPSTREAM_CHUNK_BYTES = 4
        expired_body = io.BytesIO(b'private')
        expired = HTTPError(MEDIA['url'], 403, 'private', {}, expired_body)
        self.opener.open.side_effect = [
            Upstream(b'1234', status=206, headers={'Content-Range':'bytes 0-3/12'}), expired,
            Upstream(b'5678', status=206, headers={'Content-Range':'bytes 4-7/12'}),
            Upstream(b'9012', status=206, headers={'Content-Range':'bytes 8-11/12'})]
        relay = self.stream.open(VID)
        self.assertEqual(b''.join(relay.chunks()), b'123456789012')
        self.assertEqual(self.stream.resolve.call_count, 2)
        self.assertEqual(relay.upstream.attempts, 1)
        self.assertTrue(expired_body.closed)

    def test_large_whole_file_fallback_is_rejected_if_edge_ignores_range(self):
        self.stream.UPSTREAM_CHUNK_BYTES = 4
        self.opener.open.return_value = Upstream()
        with self.assertRaises(StreamError) as error:
            self.stream.open(VID)
        self.assertEqual(error.exception.code, 'range_ignored')

    def test_safe_upstream_validation(self):
        for url in ['http://rr1.googlevideo.com/a','https://googlevideo.com.evil/a',
                    'https://127.0.0.1/a','https://user:pass@rr1.googlevideo.com/a','https://rr1.googlevideo.com:444/a']:
            self.assertFalse(valid_media_url(url))
        self.assertTrue(valid_media_url(MEDIA['url']))

    def test_redirect_cannot_escape_youtube_media_hosts(self):
        with self.assertRaises(StreamError):
            _MediaRedirect().redirect_request(None,None,302,'',{},'https://127.0.0.1/private')


class WorkerLifecycle(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.executable=Path(self.temp.name)/'fake-node'

    def worker(self, body, timeout=2):
        self.executable.write_text('#!'+sys.executable+'\n'+body)
        self.executable.chmod(0o700)
        patch=unittest.mock.patch.dict(os.environ,{'YOUTUBE_NODE':str(self.executable)})
        patch.start();self.addCleanup(patch.stop)
        stream=YouTubeStream(lambda:'',timeout=timeout)
        self.addCleanup(stream.close)
        return stream

    def test_single_worker_handles_concurrent_replies(self):
        stream=self.worker('''import sys,json
for line in sys.stdin:
 m=json.loads(line)
 print(json.dumps({'id':m['id'],'result':{'url':'https://rr1.googlevideo.com/audio'}}),flush=True)
''')
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            replies=list(pool.map(lambda _:stream.resolve(VID),range(8)))
        self.assertEqual(len(replies),8)
        pid=stream._process.pid
        stream.resolve(VID);self.assertEqual(stream._process.pid,pid)

    def test_hung_worker_is_killed_and_next_request_recovers(self):
        stream=self.worker('import time\ntime.sleep(30)\n',timeout=.1)
        with self.assertRaises(StreamError) as error:stream.resolve(VID)
        self.assertEqual(error.exception.code,'resolve_timeout');self.assertIsNone(stream._process)
        self.executable.write_text('#!'+sys.executable+'''\nimport sys,json
for line in sys.stdin:
 m=json.loads(line);print(json.dumps({'id':m['id'],'result':{'url':'https://rr1.googlevideo.com/audio'}}),flush=True)
''')
        self.assertTrue(stream.resolve(VID)['url'].startswith('https://'))

    def test_worker_death_unblocks_waiters(self):
        stream=self.worker('import sys\nsys.exit(1)\n')
        with self.assertRaises(StreamError):stream.resolve(VID)
        self.assertEqual(stream._pending,{})

    def test_signed_url_from_untrusted_worker_is_rejected(self):
        stream=self.worker('''import sys,json
for line in sys.stdin:
 m=json.loads(line);print(json.dumps({'id':m['id'],'result':{'url':'http://127.0.0.1/private'}}),flush=True)
''')
        with self.assertRaises(StreamError) as error:stream.resolve(VID)
        self.assertEqual(error.exception.code,'invalid_media')


class SharedResolver(unittest.TestCase):
    def test_two_facades_on_one_resolver_keep_separate_playback_state(self):
        resolver=Mock();resolver.resolve.return_value=dict(MEDIA)
        resolver.snapshot.return_value={'resolving':0}
        one=YouTubeStream(lambda:'',resolver=resolver,capacity=1)
        two=YouTubeStream(lambda:'',resolver=resolver,capacity=1)
        self.addCleanup(one.close);self.addCleanup(two.close)
        self.assertIsNot(one.current,two.current)
        self.assertIsNot(one.slots,two.slots)
        self.assertIsNotNone(one.snapshot()['resolver_id'])
        self.assertEqual(one.snapshot()['resolver_id'],two.snapshot()['resolver_id'])

    def test_closing_one_facade_leaves_the_shared_resolver_running(self):
        resolver=Mock();resolver.resolve.return_value=dict(MEDIA)
        one=YouTubeStream(lambda:'',resolver=resolver)
        two=YouTubeStream(lambda:'',resolver=resolver)
        self.addCleanup(one.close);self.addCleanup(two.close)
        one.close()
        two.resolve(VID)
        resolver.resolve.assert_called_once_with(VID,refresh=False)
        resolver.close.assert_not_called()

    def test_a_changed_credential_moves_the_facade_to_another_worker(self):
        credential=[MEDIA['url']];workers={}
        def resolver_for():
            return workers.setdefault(credential[0],Mock())
        stream=YouTubeStream(lambda:credential[0],resolver_for=resolver_for)
        self.addCleanup(stream.close)
        first=stream._active_resolver()
        credential[0]='https://rr2.googlevideo.com/audio'
        self.assertIsNot(stream._active_resolver(),first)
        self.assertIs(stream._active_resolver(),workers[credential[0]])

    def test_a_private_facade_still_stops_its_own_worker(self):
        stream=YouTubeStream(lambda:'')
        self.addCleanup(stream.close)
        resolver=stream._active_resolver()
        with unittest.mock.patch.object(resolver,'close') as closed:
            stream.close()
        closed.assert_called_once()


if __name__=='__main__':unittest.main()
