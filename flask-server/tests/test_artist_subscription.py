"""Regression tests for YouTube Music-authoritative artist subscriptions."""
import os
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Keep this test runnable without the external integrations installed locally.
os.environ.setdefault("SECRET_KEY", "test-secret-for-artist-subscription")
os.environ.setdefault("REMOTE_USER", "test-owner")
os.environ.setdefault("REMOTE_PASSWORD", "test-pass")
os.environ.setdefault("API_KEY", "0123456789abcdef0123456789abcdef")

if "ytmusicapi" not in sys.modules:
    ytmusicapi = types.ModuleType("ytmusicapi")
    ytmusicapi.YTMusic = type("YTMusic", (), {"__init__": lambda self, **kw: None})
    sys.modules["ytmusicapi"] = ytmusicapi
    auth = types.ModuleType("ytmusicapi.auth")
    auth_types = types.ModuleType("ytmusicapi.auth.types")
    auth_types.AuthType = type("AuthType", (), {"UNAUTHORIZED": "UNAUTHORIZED"})
    browser = types.ModuleType("ytmusicapi.auth.browser")
    browser.setup_browser = lambda *args, **kwargs: None
    sys.modules["ytmusicapi.auth"] = auth
    sys.modules["ytmusicapi.auth.types"] = auth_types
    sys.modules["ytmusicapi.auth.browser"] = browser

if "home_feed" not in sys.modules:
    sys.modules["home_feed"] = types.ModuleType("home_feed")

if "alexa_remote" not in sys.modules:
    alexa_remote = types.ModuleType("alexa_remote")
    remote = types.ModuleType("alexa_remote.remote")
    remote.devices = lambda refresh=False: (None, "stubbed")
    remote.volume = lambda serial: (None, "stubbed")
    remote.is_logged_in = lambda: (True, None)
    remote.proxy_start_url = lambda *args, **kwargs: (None, "stubbed")
    alexa_remote.remote = remote
    alexa_remote.AlexaUnreachable = type("AlexaUnreachable", (Exception,), {})
    sys.modules["alexa_remote"] = alexa_remote
    sys.modules["alexa_remote.remote"] = remote

if "youtube_browser_session" not in sys.modules:
    ybs = types.ModuleType("youtube_browser_session")
    ybs.BrowserController = type("BrowserController", (), {"__init__": lambda self, *a, **kw: None})
    ybs.YouTubeBrowserSessionManager = type("YouTubeBrowserSessionManager", (), {"__init__": lambda self, *a, **kw: None})
    ybs.browser_client_is_signed_in = lambda *args, **kwargs: False
    ybs.is_authentication_error = lambda error: (
        'login required' in str(error).lower() or
        'authentication' in str(error).lower()
    )
    ybs.promote_browser_headers = lambda *args, **kwargs: None
    sys.modules["youtube_browser_session"] = ybs

import server  # noqa: E402


class FakeYouTubeMusic:
    auth_type = "BROWSER"

    def __init__(self, remote_artists=None):
        self.remote_artists = list(remote_artists or [])
        self.subscribed = []
        self.unsubscribed = []

    def subscribe_artists(self, channel_ids):
        self.subscribed.append(channel_ids)
        channel_id = channel_ids[0]
        self.remote_artists.insert(0, {
            'browseId': channel_id,
            'artist': 'Test Artist',
            'thumbnails': [{'url': 'thumb'}],
        })

    def unsubscribe_artists(self, channel_ids):
        self.unsubscribed.append(channel_ids)
        ids = set(channel_ids)
        self.remote_artists = [a for a in self.remote_artists
                               if a.get('browseId') not in ids]

    def get_library_subscriptions(self, limit, order):
        return list(self.remote_artists)


class ArtistSubscriptionEndpoint(unittest.TestCase):
    def setUp(self):
        server._ensure_db()
        with server.get_db() as conn:
            conn.execute("DELETE FROM kv WHERE k IN ('subscribed_artists', 'unsubscribed_artists')")
            conn.commit()
        self.client = server.app.test_client()

    def tearDown(self):
        with server.get_db() as conn:
            conn.execute("DELETE FROM kv WHERE k IN ('subscribed_artists', 'unsubscribed_artists')")
            conn.commit()

    def _request(self, method, path, fake, **kwargs):
        with mock.patch.object(server, '_logged_in', return_value=True), \
             mock.patch.object(server, '_jam_guest', return_value=False), \
             mock.patch.object(server, '_get_ytmusic_home', return_value=fake), \
             mock.patch.object(server, '_ytmusic_client_is_authenticated', return_value=True), \
             mock.patch.object(server, '_with_youtube_auth_renewal',
                               side_effect=lambda operation: operation()):
            return self.client.open(path, method=method, **kwargs)

    def test_subscribe_mutates_and_returns_confirmed_youtube_state(self):
        fake = FakeYouTubeMusic()
        response = self._request(
            'POST', '/api/subscribed_artists/', fake,
            json={'channel_id': 'UC_TEST', 'name': 'Test Artist', 'thumbnail': 'thumb'},
        )
        self.assertIn(response.status_code, (200, 202))
        self.assertEqual(fake.subscribed, [['UC_TEST']])
        self.assertTrue(response.get_json()['subscribed'])
        with server.get_db() as conn:
            self.assertIsNone(conn.execute(
                "SELECT v FROM kv WHERE k = 'subscribed_artists'"
            ).fetchone())

    def test_unsubscribe_mutates_and_returns_empty_confirmed_youtube_state(self):
        fake = FakeYouTubeMusic([{
            'browseId': 'UC_TEST', 'artist': 'Test Artist',
            'thumbnails': [{'url': 'thumb'}],
        }])
        response = self._request(
            'DELETE', '/api/subscribed_artists/?channel_id=UC_TEST', fake,
        )
        self.assertIn(response.status_code, (200, 202))
        self.assertEqual(fake.unsubscribed, [['UC_TEST']])
        self.assertFalse(response.get_json()['subscribed'])

    def test_frontend_preserves_pending_subscription_state(self):
        artist_source = Path(__file__).parents[1].joinpath(
            'templates', 'static', 'js', 'artist.js'
        ).read_text()
        self.assertIn('result.pending_confirmation', artist_source)
        self.assertIn('Array.isArray(result.artists)', artist_source)

    def test_frontend_delete_places_channel_id_in_query_string(self):
        artist_source = Path(__file__).parents[1].joinpath(
            'templates', 'static', 'js', 'artist.js'
        ).read_text()
        self.assertIn(
            "window.apiDelete('/api/subscribed_artists/?channel_id=' + encodeURIComponent(channelId))",
            artist_source,
        )

    def test_youtube_failure_does_not_create_local_artist(self):
        fake = FakeYouTubeMusic()
        fake.subscribe_artists = mock.Mock(side_effect=RuntimeError('upstream failed'))

        response = self._request(
            'POST', '/api/subscribed_artists/', fake,
            json={'channel_id': 'UC_TEST', 'name': 'Test Artist'},
        )
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.get_json()['error']['code'], 'youtube_subscription_failed')
        with server.get_db() as conn:
            self.assertIsNone(conn.execute(
                "SELECT v FROM kv WHERE k IN ('subscribed_artists', 'unsubscribed_artists')"
            ).fetchone())

    def test_get_uses_youtube_only_and_does_not_return_stale_local_artist(self):
        fake = FakeYouTubeMusic([])
        with server.get_db() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO kv (k, v) VALUES ('subscribed_artists', ?)",
                ('[{"channel_id":"UC_GHOST","name":"Ghost"}]',),
            )
            conn.commit()
        response = self._request('GET', '/api/subscribed_artists/', fake)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['artists'], [])

    def test_auth_failure_is_actionable(self):
        fake = FakeYouTubeMusic()
        fake.subscribe_artists = mock.Mock(
            side_effect=RuntimeError('This operation requires authentication')
        )
        response = self._request(
            'POST', '/api/subscribed_artists/', fake,
            json={'channel_id': 'UC_TEST', 'name': 'Test Artist'},
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()['error']['code'], 'youtube_auth_required')

    def test_get_failure_is_not_silently_rendered_as_empty_library(self):
        fake = FakeYouTubeMusic()
        fake.get_library_subscriptions = mock.Mock(
            side_effect=RuntimeError('upstream failed')
        )
        response = self._request('GET', '/api/subscribed_artists/', fake)
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.get_json()['error']['code'], 'youtube_subscription_failed')

    def test_mutation_returns_pending_when_youtube_read_is_stale(self):
        fake = FakeYouTubeMusic()
        fake.subscribe_artists = mock.Mock()
        fake.get_library_subscriptions = mock.Mock(return_value=[])
        response = self._request(
            'POST', '/api/subscribed_artists/', fake,
            json={'channel_id': 'UC_TEST', 'name': 'Test Artist'},
        )
        self.assertEqual(response.status_code, 202)
        self.assertTrue(response.get_json()['subscribed'])
        self.assertTrue(response.get_json()['pending_confirmation'])
        self.assertNotIn('artists', response.get_json())


if __name__ == '__main__':
    unittest.main()
