"""Download-cookie policy: players have no upload door, and cookie selection
only trusts a jar that actually holds live YouTube cookies."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from test_next_track import server

EXPORT = '# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t2000000000\tSID\tvalue\n'


class DownloadCookieRoutes(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Path(self.temp.name) / 'download.txt'
        patch = mock.patch.dict(os.environ, {'YTDLP_COOKIE_STORE': str(self.store)})
        patch.start(); self.addCleanup(patch.stop)
        self.client = server.app.test_client()

    def test_players_cannot_replace_the_shared_jar(self):
        # The door is gone for everyone, not merely hidden in the menu. Use the
        # owner key so this asserts the missing route rather than API auth.
        headers = {'X-Api-Key': server.API_KEY}
        self.assertEqual(self.client.post('/api/youtube/download-cookies',
                                          json={'cookies': EXPORT}, headers=headers).status_code, 404)
        self.assertEqual(self.client.post('/api/youtube/download-cookies',
                                          data='cookies=x', headers=headers).status_code, 404)

    def test_audio_probe_is_administrator_only(self):
        with mock.patch.object(server, '_admin_logged_in', return_value=False), \
                mock.patch.object(server, '_logged_in', return_value=True):
            self.assertEqual(self.client.get(
                '/api/youtube/download-cookies/status').status_code, 401)
        with mock.patch.object(server, '_admin_logged_in', return_value=True), \
                mock.patch.object(server, '_logged_in', return_value=True), \
                mock.patch.object(server.download_cookies, 'status',
                                  return_value={'valid': True, 'message': 'ok'}) as probe:
            response = self.client.get('/api/youtube/download-cookies/status?refresh=1')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(probe.call_args.kwargs['force'])

    def test_saved_cookie_takes_precedence_over_read_only_mount(self):
        self.store.write_text(EXPORT)
        with mock.patch.dict(os.environ, {'YTDLP_COOKIES': str(Path(self.temp.name)/'original.txt')}):
            self.assertEqual(server.Supporting.get_ytdlp_cookies_file(), str(self.store))

    def test_empty_saved_file_does_not_shadow_a_usable_mount(self):
        self.store.write_text('')
        original = Path(self.temp.name) / 'original.txt'
        original.write_text(EXPORT)
        with mock.patch.dict(os.environ, {'YTDLP_COOKIES': str(original)}):
            self.assertEqual(server.Supporting.get_ytdlp_cookies_file(), str(original))


if __name__ == '__main__':
    unittest.main()
