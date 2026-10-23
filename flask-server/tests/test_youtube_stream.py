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
        self.assertIsNone(self.opener.open.call_args.args[0].get_header('Range'));relay.close()

    def test_capacity_is_bounded_and_recovers_after_disconnect(self):
        self.opener.open.return_value=Upstream()
        relay=self.stream.open(VID)
        with self.assertRaises(StreamError) as e:self.stream.open(VID)
        self.assertEqual(e.exception.code,'busy');relay.close()
        self.opener.open.return_value=Upstream();self.stream.open(VID).close()

    def test_read_failure_releases_slot(self):
        up=Upstream();self.opener.open.return_value=up
        relay=self.stream.open(VID)
        up.read1=Mock(side_effect=OSError('broken connection'))
        with self.assertRaises(OSError):list(relay.chunks())
        self.assertTrue(up.closed);self.assertTrue(self.stream.slots.acquire(False));self.stream.slots.release()

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


if __name__=='__main__':unittest.main()
