"""Cookie APIs: owner/key auth, rejected saves, and playback cookie selection."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from test_next_track import server


class DownloadCookieRoutes(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Path(self.temp.name) / 'download.txt'
        patch = mock.patch.dict(os.environ, {'YTDLP_COOKIE_STORE': str(self.store)})
        patch.start(); self.addCleanup(patch.stop)
        self.client = server.app.test_client()
        self.url = '/api/youtube/download-cookies?key=' + server.API_KEY

    def test_unauthenticated_upload_and_status_rejected(self):
        self.assertEqual(self.client.post('/api/youtube/download-cookies', json={'cookies':'secret'}).status_code, 401)
        self.assertEqual(self.client.get('/api/youtube/download-cookies/status').status_code, 401)

    def test_key_upload_rejects_invalid_download(self):
        with mock.patch.object(server.download_cookies, 'replace', return_value={'valid':False,'message':'download blocked'}):
            self.assertEqual(self.client.post(self.url, json={'cookies':'candidate'}).status_code, 422)

    def test_success_clears_flaky_cache_and_returns_no_cookie_values(self):
        server._mark_video_flaky('AAAAAAAAAAA')
        with mock.patch.object(server.download_cookies, 'replace', return_value={'valid':True,'message':'Audio downloaded'}):
            response = self.client.post(self.url, json={'cookies':'secret-cookie-value'})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()['success'])
        self.assertNotIn('secret-cookie-value', response.get_data(as_text=True))
        self.assertFalse(server._is_flaky_video('AAAAAAAAAAA'))

    def test_owner_session_can_replace_but_guest_and_non_json_cannot(self):
        with mock.patch.object(server, '_logged_in', return_value=True), mock.patch.object(
                server.download_cookies, 'replace', return_value={'valid':True,'message':'ok'}):
            self.assertEqual(self.client.post('/api/youtube/download-cookies', json={'cookies':'candidate'}).status_code, 200)
            self.assertEqual(self.client.post('/api/youtube/download-cookies', data='cookies=candidate').status_code, 401)
        with mock.patch.object(server, '_logged_in', return_value=False), mock.patch.object(server, '_jam_guest', return_value=True):
            self.assertEqual(self.client.post('/api/youtube/download-cookies', json={'cookies':'candidate'}).status_code, 401)

    def test_saved_cookie_takes_precedence_over_read_only_mount(self):
        self.store.write_text('saved cookie')
        with mock.patch.dict(os.environ, {'YTDLP_COOKIES': str(Path(self.temp.name)/'original.txt')}):
            self.assertEqual(server.Supporting.get_ytdlp_cookies_file(), str(self.store))

    def test_forced_audio_status(self):
        with mock.patch.object(server.download_cookies, 'status', return_value={'valid':True,'message':'ok'}) as probe:
            response = self.client.get('/api/youtube/download-cookies/status?refresh=1&key='+server.API_KEY)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(probe.call_args.kwargs['force'])


if __name__ == '__main__':
    unittest.main()
