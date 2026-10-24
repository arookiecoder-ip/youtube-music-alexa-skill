"""Both player routes retain auth/ownership while bypassing file downloads."""
import os
import tempfile
import unittest
from unittest import mock
import test_audio_endpoint as baseline
from test_youtube_stream import Upstream
from youtube_stream import Relay, StreamError

server = baseline.server
VID = 'dQw4w9WgXcQ'


class StreamRoutes(unittest.TestCase):
    def setUp(self):
        self.client=server.app.test_client()
        self.headers={'X-Api-Key':server.API_KEY}
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.release=mock.Mock()
        self.upstream=Upstream()
        self.relay=Relay(self.upstream,b'audio',self.release)
        for name,value in [('enabled',True),('open',mock.Mock(return_value=self.relay))]:
            patch=mock.patch.object(server._youtube_stream,name,value);patch.start();self.addCleanup(patch.stop)
        self.addCleanup(self.relay.close)

    def test_audio_wait_hint_streams_without_disk_or_echo_side_effects(self):
        with mock.patch.object(server,'_confirm_stream_delivery') as confirm:
            r=self.client.get('/audio/?video_id='+VID+'&wait=1',headers=self.headers)
            self.assertEqual(r.status_code,200);self.assertNotIn('X-Cache',r.headers)
            self.assertTrue(r.data.startswith(b'audio'));r.close();confirm.assert_not_called()
        self.assertEqual(os.listdir(self.temp.name),[])

    def test_unauthorized_requests_never_resolve_media(self):
        r=self.client.get('/audio/?video_id='+VID)
        self.assertEqual(r.status_code,401);server._youtube_stream.open.assert_not_called()

    def test_head_releases_upstream_and_preserves_length(self):
        r=self.client.head('/audio/?video_id='+VID,headers=self.headers)
        self.assertEqual(r.status_code,200);self.assertEqual(r.data,b'')
        self.assertEqual(r.headers['Content-Length'],'12');self.assertTrue(self.upstream.closed)
        self.release.assert_called_once()

    def test_range_and_if_range_are_forwarded_to_relay(self):
        r=self.client.get('/audio/?video_id='+VID,headers={**self.headers,'Range':'bytes=3-','If-Range':'"same"'})
        server._youtube_stream.open.assert_called_once_with(VID,method='GET',byte_range='bytes=3-',if_range='"same"');r.close()

    def test_upstream_failure_is_structured_and_retryable(self):
        server._youtube_stream.open.side_effect=StreamError('resolve_timeout')
        r=self.client.get('/audio/?video_id='+VID,headers=self.headers)
        self.assertEqual(r.status_code,503);self.assertTrue(r.json['error']['retryable'])
        self.assertEqual(r.headers['Retry-After'],'2');self.assertNotIn('googlevideo',r.get_data(as_text=True))

    def test_prefetch_only_prepares_url(self):
        with mock.patch.object(server,'_ensure_audio_ready_for_play') as prepare:
            r=self.client.get('/audio/?video_id='+VID+'&prefetch=1',headers=self.headers)
            self.assertEqual(r.status_code,202);prepare.assert_called_once_with(VID,prefetch=True)
            server._youtube_stream.open.assert_not_called()

    def test_preparation_never_schedules_file_download(self):
        with mock.patch.object(server._audio_jobs,'submit') as submit, mock.patch.object(server._youtube_stream,'resolve') as resolve:
            server._ensure_audio_ready_for_play(VID)
            submit.call_args.args[1]()
            resolve.assert_called_once_with(VID)

    def test_closed_uniterated_response_releases_stream(self):
        with server.app.test_request_context('/audio/?video_id='+VID):
            r=server._serve_youtube_stream(VID)
            r.close()
        self.release.assert_called_once();self.assertTrue(self.upstream.closed)

    def test_stale_echo_transfer_cannot_confirm_new_playback(self):
        with server.app.test_request_context('/proxy/?video_id='+VID), \
             mock.patch.object(server,'_generation_superseded',return_value=True), \
             mock.patch.object(server,'_stream_superseded',return_value=True), \
             mock.patch.object(server,'_confirm_stream_delivery') as confirm:
            r=server._serve_youtube_stream(VID,confirm=True,cancellable=True)
            self.assertEqual(b''.join(r.response),b'');r.close();confirm.assert_not_called()

    def test_explicit_download_streams_with_an_attachment_filename(self):
        before=server._youtube_stream.current.video
        r=self.client.get('/audio/?video_id='+VID,headers={**self.headers,'X-MusicBox-Download':'1'})
        self.assertEqual(r.status_code,200)
        self.assertIn('attachment',r.headers['Content-Disposition'])
        self.assertTrue(r.data.startswith(b'audio'));r.close()
        self.assertEqual(server._youtube_stream.current.video,before)


if __name__=='__main__':unittest.main()
