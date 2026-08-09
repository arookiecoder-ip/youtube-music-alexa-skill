import os
import sys
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Keep this focused test independently importable in the same lightweight
# environment as the existing routing test. The application dependencies are
# supplied by the project's test environment/container.
_original_modules = {name: sys.modules.get(name) for name in (
    'ytmusicapi', 'home_feed', 'alexa_remote', 'youtube_browser_session')}
_original_env = os.environ.copy()
for name in ('ytmusicapi', 'home_feed', 'alexa_remote', 'youtube_browser_session'):
    sys.modules[name] = types.ModuleType(name)
sys.modules['ytmusicapi'].YTMusic = type('YTMusic', (), {})
sys.modules['home_feed'] = types.ModuleType('home_feed')
sys.modules['alexa_remote'].AlexaUnreachable = type('AlexaUnreachable', (Exception,), {})
for attr in ('BrowserController', 'YouTubeBrowserSessionManager'):
    setattr(sys.modules['youtube_browser_session'], attr, type(
        attr, (), {'__init__': lambda self, *a, **k: None}))
sys.modules['youtube_browser_session'].browser_client_is_signed_in = lambda *a, **k: False
sys.modules['youtube_browser_session'].is_authentication_error = lambda *a, **k: False
sys.modules['youtube_browser_session'].promote_browser_headers = lambda *a, **k: None

import server  # noqa: E402

# Restore the host process modules/environment after server import so this test
# cannot contaminate neighboring test modules.
os.environ.clear()
os.environ.update(_original_env)
for _name, _module in _original_modules.items():
    if _module is None:
        sys.modules.pop(_name, None)
    else:
        sys.modules[_name] = _module


class ArtistNotFound(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = server.app.test_client()

    def _request_artist(self, error, *, browse_response=None):
        fake = mock.Mock()
        fake.get_artist.side_effect = error
        if browse_response is not None:
            fake._send_request.return_value = browse_response
        with mock.patch.object(server, '_logged_in', return_value=True), \
             mock.patch.object(server, '_jam_guest', return_value=False), \
             mock.patch.object(server, '_get_ytmusic_home', return_value=fake):
            return self.client.get('/api/artist/UCchannel-id')

    def test_invalid_artist_returns_sanitized_404(self):
        response = self._request_artist(Exception('Invalid id: channel not found'))
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json()['error']['code'], 'not_found')
        self.assertNotIn('Invalid id', response.get_data(as_text=True))

    def test_invalid_argument_anonymous_fallback_returns_404(self):
        authenticated = mock.Mock()
        authenticated.get_artist.side_effect = Exception('Invalid argument: bad browse id')
        # The endpoint uses the module-level YTMusic class for its anonymous
        # retry, so the fallback must be patched as a class method (an
        # instance-level patch of server.YTMusic would be shadowed).
        anonymous = mock.Mock(side_effect=Exception('Invalid id: channel not found'))
        with mock.patch.object(server, '_logged_in', return_value=True), \
             mock.patch.object(server, '_jam_guest', return_value=False), \
             mock.patch.object(server, '_get_ytmusic_home', return_value=authenticated), \
             mock.patch.object(server.YTMusic, 'get_artist', anonymous, create=True):
            response = self.client.get('/api/artist/UCchannel-id')
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json()['error']['code'], 'not_found')
        self.assertEqual(anonymous.call_count, 1)

    def test_empty_artist_fallback_without_header_returns_404(self):
        # ytmusicapi reports both empty pages and unknown channels as missing
        # content. An unknown channel has no header to render, so it must not
        # become a fake successful artist shell followed by a frontend toast.
        response = self._request_artist(Exception("Unable to find 'content'"), browse_response={})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json()['error']['code'], 'not_found')

    def test_artist_provider_failure_returns_502(self):
        response = self._request_artist(Exception('connection refused'))
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.get_json()['error']['code'], 'bad_gateway')

    def test_unrelated_not_found_payload_returns_502(self):
        response = self._request_artist(
            Exception("response field 'artist' not found in payload"))
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.get_json()['error']['code'], 'bad_gateway')
        self.assertNotIn('response field', response.get_data(as_text=True))

    def test_invalid_artist_songs_returns_sanitized_404(self):
        fake = mock.Mock()
        fake.get_playlist.side_effect = Exception('Invalid id: playlist not found')
        with mock.patch.object(server, '_logged_in', return_value=True), \
             mock.patch.object(server, '_jam_guest', return_value=False), \
             mock.patch.object(server, '_get_ytmusic_home', return_value=fake), \
             mock.patch.object(server, 'YTMusic', return_value=fake):
            response = self.client.get(
                '/api/artist/UCchannel-id/songs?browse_id=invalid-browse-id')
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json()['error']['code'], 'not_found')

    def test_artist_songs_provider_failure_returns_502(self):
        fake = mock.Mock()
        fake.get_playlist.side_effect = Exception('connection refused')
        with mock.patch.object(server, '_logged_in', return_value=True), \
             mock.patch.object(server, '_jam_guest', return_value=False), \
             mock.patch.object(server, '_get_ytmusic_home', return_value=fake), \
             mock.patch.object(server, 'YTMusic', return_value=fake):
            response = self.client.get(
                '/api/artist/UCchannel-id/songs?browse_id=valid-browse-id')
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.get_json()['error']['code'], 'bad_gateway')


if __name__ == '__main__':
    unittest.main()
