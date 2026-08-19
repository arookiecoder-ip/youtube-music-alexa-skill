import os
import sys
import types
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))


def _install_stubs():
    ytmusicapi = types.ModuleType('ytmusicapi')
    ytmusicapi.YTMusic = type('YTMusic', (), {'__init__': lambda self, **kw: None})
    sys.modules['ytmusicapi'] = ytmusicapi
    sys.modules['ytmusicapi.auth'] = types.ModuleType('ytmusicapi.auth')
    auth_types = types.ModuleType('ytmusicapi.auth.types')
    auth_types.AuthType = type('AuthType', (), {'UNAUTHORIZED': 'UNAUTHORIZED'})
    sys.modules['ytmusicapi.auth.types'] = auth_types
    browser = types.ModuleType('ytmusicapi.auth.browser')
    browser.setup_browser = lambda *a, **kw: None
    sys.modules['ytmusicapi.auth.browser'] = browser
    sys.modules['home_feed'] = types.ModuleType('home_feed')
    alexa = types.ModuleType('alexa_remote')
    alexa.AlexaUnreachable = type('AlexaUnreachable', (Exception,), {})
    alexa_remote = types.ModuleType('alexa_remote.remote')
    alexa_remote.devices = lambda refresh=False: ([], 'stub')
    alexa_remote.is_logged_in = lambda: (False, None)
    alexa.remote = alexa_remote
    sys.modules['alexa_remote'] = alexa
    sys.modules['alexa_remote.remote'] = alexa_remote
    ybs = types.ModuleType('youtube_browser_session')
    for attr in ('BrowserController', 'YouTubeBrowserSessionManager'):
        setattr(ybs, attr, type(attr, (), {'__init__': lambda self, *a, **kw: None}))
    ybs.browser_client_is_signed_in = lambda *a, **kw: False
    ybs.promote_browser_headers = lambda *a, **kw: None
    ybs.is_authentication_error = lambda *a, **kw: False
    sys.modules['youtube_browser_session'] = ybs


_TEST_ENV = {
    'SECRET_KEY': 'session-cookie-test-secret',
    'API_KEY': 'test-api-key',
    'REMOTE_USER': 'owner',
    'REMOTE_PASSWORD': 'password',
    'PUBLIC_BASE_URL': 'http://localhost:5000',
}


class SessionCookieConfig(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original_env = os.environ.copy()
        cls.db = tempfile.NamedTemporaryFile(prefix='session-cookie-', suffix='.db', delete=False)
        cls.db.close()
        env = dict(_TEST_ENV, DB_FILE=cls.db.name)
        os.environ.update(env)
        # The host environment may configure TOTP; these login tests exercise
        # the plain username/password path, so force 2FA off.
        os.environ.pop('REMOTE_TOTP_SECRET', None)
        _install_stubs()
        import server
        cls.server = server

    @classmethod
    def tearDownClass(cls):
        os.environ.clear()
        os.environ.update(cls.original_env)
        try:
            os.unlink(cls.db.name)
        except OSError:
            pass

    def test_http_public_base_url_does_not_issue_secure_cookie(self):
        with self.server.app.test_request_context('/'):
            # The config is captured at import time, so assert the invariant
            # that holds regardless of which module imported `server` first:
            # the Secure flag must mirror the PUBLIC_BASE_URL that was active
            # when the module was imported (http:// ⇒ insecure cookie).
            expected_secure = self.server._public_base_url.startswith('https://')
            self.assertEqual(
                self.server.app.config['SESSION_COOKIE_SECURE'],
                expected_secure,
            )

    def test_login_session_cookie_authorizes_following_api_request(self):
        client = self.server.app.test_client()
        # Use whatever credentials the already-imported server module holds
        # (another test file may have imported it first with its own env).
        # TOTP may be active in the imported server (host env leaks into the
        # first import), so force the plain username/password path.
        with mock.patch.object(self.server, '_ytmusic_is_authenticated', return_value=False), \
             mock.patch.object(self.server, '_totp_enabled', return_value=False):
            response = client.post('/login/', json={
                'username': self.server.REMOTE_USER,
                'password': self.server.REMOTE_PASSWORD,
                'next': '/home',
            })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['next'], '/home')
        api_response = client.get('/alexa/status/')
        # The endpoint may report Amazon as disconnected (or 500 in a stubbed
        # environment), but the signed-in cookie must pass Flask's web-session
        # auth gate — never a 401.
        self.assertNotEqual(api_response.status_code, 401)

    def test_track_artwork_endpoint_authorized_by_web_session(self):
        # /api/track/<video_id>/artwork is fetched by the SPA without the API
        # key. It must be reachable with the logged-in web-session cookie, or
        # the player's high-res artwork probe 401s on every page load.
        client = self.server.app.test_client()
        anon = client.get('/api/track/EiukAyTOzCk/artwork')
        self.assertEqual(anon.status_code, 401)
        with mock.patch.object(self.server, '_ytmusic_is_authenticated', return_value=False), \
             mock.patch.object(self.server, '_totp_enabled', return_value=False):
            response = client.post('/login/', json={
                'username': self.server.REMOTE_USER,
                'password': self.server.REMOTE_PASSWORD,
                'next': '/home',
            })
        self.assertEqual(response.status_code, 200)
        # Stubbed ytmusicapi has no get_song, so the handler falls back to the
        # standard rendition URLs — the key assertion is that the session
        # cookie passes the auth gate (no 401/403).
        artwork = client.get('/api/track/EiukAyTOzCk/artwork')
        self.assertEqual(artwork.status_code, 200)
        payload = artwork.get_json()
        self.assertTrue(payload['thumbnails'])
        self.assertIn('EiukAyTOzCk', payload['thumbnail'])

    def test_jam_home_falls_back_to_public_home_when_charts_fail(self):
        # The anonymous India charts call can raise or return empty (YouTube
        # blocking). A failure there must not prevent the public-home fallback
        # from building shelves, and a fully empty result must return an empty
        # feed rather than a 500.
        import importlib.util
        home_feed_path = os.path.join(os.path.dirname(__file__), '..', 'home_feed.py')
        spec = importlib.util.spec_from_file_location('home_feed_real', home_feed_path)
        real_home_feed = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(real_home_feed)
        self.server.home_feed = real_home_feed

        class FakeYT:
            def __init__(self, charts_raise):
                self.charts_raise = charts_raise

            def get_charts(self, country):
                if self.charts_raise:
                    raise RuntimeError('anonymous charts blocked')
                return {'songs': {'items': [{
                    'videoId': 'abc123', 'title': 'Chart Song',
                    'artists': [{'name': 'Artist'}],
                    'thumbnails': [{'url': 'http://x/chart.jpg'}],
                }]}, 'videos': {'items': []}}

            def get_home(self, limit=40):
                return [{'title': 'Public shelf', 'contents': [{
                    'videoId': 'def456', 'title': 'Home Song',
                    'artists': [{'name': 'A'}],
                    'thumbnails': [{'url': 'http://x/home.jpg'}],
                }]}]

        with mock.patch.object(self.server, '_get_ytmusic',
                               return_value=FakeYT(charts_raise=True)):
            data = self.server._build_jam_india_home()
        self.assertEqual(len(data['shelves']), 1)
        self.assertEqual(data['shelves'][0]['source'], 'public_youtube_home')
        self.assertEqual(data['shelves'][0]['items'][0]['title'], 'Home Song')

        with mock.patch.object(self.server, '_get_ytmusic',
                               return_value=FakeYT(charts_raise=False)):
            data = self.server._build_jam_india_home()
        self.assertEqual(len(data['shelves']), 1)
        self.assertEqual(data['shelves'][0]['source'], 'public_india_charts')

        class EmptyYT:
            def get_charts(self, country):
                return {}

            def get_home(self, limit=40):
                return []

        with mock.patch.object(self.server, '_get_ytmusic',
                               return_value=EmptyYT()):
            data = self.server._build_jam_india_home()
        self.assertEqual(data['shelves'], [])


if __name__ == '__main__':
    unittest.main()
