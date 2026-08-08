"""Regression tests for wrong-artist parsing.

Bug: some tracks showed the wrong "artist" in the web remote / home feed.

Cause: ytmusicapi's `artists` list on a search/radio/chart/videos-filter hit
can contain an uploader "Channel - Topic" name, or a generic YouTube metadata
label ("Release", "Album", ...) instead of the real recording artist. One code
path (`_normalize_artist_song_results` in server.py) already filtered these out
via `_clean_artist_credit`, but at least six other places joined the raw
`artists[].name` values unfiltered:
  - Supporting.get_radiolist (search seed track)
  - Supporting.get_charts_queue
  - Supporting.get_radio_queue
  - Supporting.get_artist
  - Supporting.get_album
  - Supporting.get_playlist_tracks
  - the artist page's per-track fallback in api_get_artist / _normalize... (t_artist)
  - home_feed.normalize_track (home feed cards)

Fix: a shared `_artist_credit_from_list()` helper (server.py) / `_clean_artist_name()`
(home_feed.py) that strips "- Topic" suffixes and generic labels before joining,
now used by every one of those call sites.

Run with ``pytest test_artist_credit_parsing.py`` or
``python -m unittest test_artist_credit_parsing``.
"""
import os
import sys
import types
import unittest

# ---------------------------------------------------------------------------
# home_feed.py has no heavy dependencies, so it can be imported directly.
# ---------------------------------------------------------------------------
import home_feed


class NormalizeTrackArtistCleaning(unittest.TestCase):
    def test_strips_topic_channel_suffix(self):
        item = {
            "videoId": "abc123",
            "title": "Some Song",
            "artists": [{"name": "Real Artist - Topic", "id": "UC123"}],
        }
        track = home_feed.normalize_track(item)
        self.assertEqual(track["artists"], [{"name": "Real Artist", "id": "UC123"}])
        self.assertIn("Real Artist", track["subtitle"])
        self.assertNotIn("Topic", track["subtitle"])

    def test_drops_generic_label_but_keeps_other_real_artists(self):
        item = {
            "videoId": "abc123",
            "title": "Some Song",
            "artists": [{"name": "Release", "id": ""},
                       {"name": "Real Artist", "id": "UC123"}],
        }
        track = home_feed.normalize_track(item)
        self.assertEqual([a["name"] for a in track["artists"]], ["Real Artist"])

    def test_all_generic_yields_empty_artist_list_not_a_crash(self):
        item = {
            "videoId": "abc123",
            "title": "Some Song",
            "artists": [{"name": "Album", "id": ""}],
        }
        track = home_feed.normalize_track(item)
        self.assertEqual(track["artists"], [])
        self.assertEqual(track["artistId"], "")
        # subtitle falls back to just the album (or empty), never crashes.
        self.assertNotIn("Album", track["subtitle"].split(" \u2022 ")[0:0])

    def test_normal_artist_name_untouched(self):
        item = {
            "videoId": "abc123",
            "title": "Some Song",
            "artists": [{"name": "Taylor Swift", "id": "UC999"}],
        }
        track = home_feed.normalize_track(item)
        self.assertEqual(track["artists"], [{"name": "Taylor Swift", "id": "UC999"}])

    def test_case_insensitive_generic_label_match(self):
        item = {
            "videoId": "abc123",
            "title": "Some Song",
            "artists": [{"name": "RELEASE", "id": ""}],
        }
        track = home_feed.normalize_track(item)
        self.assertEqual(track["artists"], [])

    def test_missing_artists_list_does_not_crash(self):
        item = {"videoId": "abc123", "title": "No Artists"}
        track = home_feed.normalize_track(item)
        self.assertEqual(track["artists"], [])
        self.assertEqual(track["artistId"], "")

    def test_non_dict_artist_entries_are_skipped(self):
        item = {
            "videoId": "abc123",
            "title": "Weird Shape",
            "artists": ["not-a-dict", None, {"name": "Real Artist"}],
        }
        track = home_feed.normalize_track(item)
        self.assertEqual([a["name"] for a in track["artists"]], ["Real Artist"])

    def test_topic_suffix_with_only_whitespace_left_is_dropped(self):
        item = {
            "videoId": "abc123",
            "title": "Edge",
            "artists": [{"name": " - Topic", "id": ""}],
        }
        track = home_feed.normalize_track(item)
        self.assertEqual(track["artists"], [])


# ---------------------------------------------------------------------------
# server.py's _artist_credit_from_list / _clean_artist_credit — stub the heavy
# native deps first (ytmusicapi/alexapy aren't installable here), matching the
# convention in test_download_backpressure.py / test_routing_paste_url.py.
# ---------------------------------------------------------------------------

_TEST_ENV = {
    "SECRET_KEY": "test-secret-for-artist-credit-parsing",
    "REMOTE_USER": "test-owner",
    "REMOTE_PASSWORD": "test-pass",
    "API_KEY": "0123456789abcdef0123456789abcdef",
}


def _install_stubs():
    for key, value in _TEST_ENV.items():
        os.environ.setdefault(key, value)

    if "ytmusicapi" not in sys.modules:
        ytmusicapi = types.ModuleType("ytmusicapi")
        ytmusicapi.YTMusic = type("YTMusic", (), {"__init__": lambda self, **kw: None})
        sys.modules["ytmusicapi"] = ytmusicapi

        ytmusicapi_auth = types.ModuleType("ytmusicapi.auth")
        ytmusicapi_auth_types = types.ModuleType("ytmusicapi.auth.types")
        ytmusicapi_auth_types.AuthType = type(
            "AuthType", (), {"UNAUTHORIZED": "UNAUTHORIZED"})
        ytmusicapi_auth_browser = types.ModuleType("ytmusicapi.auth.browser")
        ytmusicapi_auth_browser.setup_browser = lambda *a, **kw: None
        sys.modules["ytmusicapi.auth"] = ytmusicapi_auth
        sys.modules["ytmusicapi.auth.types"] = ytmusicapi_auth_types
        sys.modules["ytmusicapi.auth.browser"] = ytmusicapi_auth_browser

    sys.modules.setdefault("home_feed", home_feed)

    if "alexa_remote" not in sys.modules:
        alexa_remote = types.ModuleType("alexa_remote")
        alexa_remote.AlexaUnreachable = type("AlexaUnreachable", (Exception,), {})
        alexa_remote_remote = types.ModuleType("alexa_remote.remote")
        alexa_remote_remote.devices = lambda refresh=False: (None, "stubbed-no-devices")
        alexa_remote_remote.volume = lambda serial: (None, "stubbed-no-devices")
        alexa_remote_remote.is_logged_in = lambda: (True, None)
        alexa_remote_remote.proxy_start_url = lambda *a, **kw: (None, "stubbed")
        alexa_remote.remote = alexa_remote_remote
        sys.modules["alexa_remote"] = alexa_remote
        sys.modules["alexa_remote.remote"] = alexa_remote_remote

    if "youtube_browser_session" not in sys.modules:
        try:
            import youtube_browser_session  # noqa: F401
        except Exception:
            ybs = types.ModuleType("youtube_browser_session")
            for attr in ("BrowserController", "YouTubeBrowserSessionManager"):
                setattr(ybs, attr,
                        type(attr, (), {"__init__": lambda self, *a, **kw: None}))
            ybs.browser_client_is_signed_in = lambda *a, **kw: False
            ybs.promote_browser_headers = lambda *a, **kw: None
            sys.modules["youtube_browser_session"] = ybs


_install_stubs()
import server  # noqa: E402 — only after the stubs are installed


class ArtistCreditFromListTests(unittest.TestCase):
    def test_strips_topic_suffix(self):
        artists = [{"name": "Real Artist - Topic"}]
        self.assertEqual(server._artist_credit_from_list(artists), "Real Artist")

    def test_drops_generic_labels(self):
        artists = [{"name": "Release"}, {"name": "Real Artist"}]
        self.assertEqual(server._artist_credit_from_list(artists), "Real Artist")

    def test_joins_multiple_real_artists_with_and(self):
        artists = [{"name": "Artist A"}, {"name": "Artist B"}]
        self.assertEqual(server._artist_credit_from_list(artists), "Artist A and Artist B")

    def test_empty_list_uses_fallback(self):
        self.assertEqual(server._artist_credit_from_list([], fallback="Search Query"),
                         "Search Query")

    def test_none_uses_fallback(self):
        self.assertEqual(server._artist_credit_from_list(None, fallback="Search Query"),
                         "Search Query")

    def test_all_generic_uses_fallback_not_empty_join(self):
        artists = [{"name": "Album"}, {"name": "Music"}]
        self.assertEqual(server._artist_credit_from_list(artists, fallback="Fallback Name"),
                         "Fallback Name")

    def test_default_fallback_is_empty_string(self):
        artists = [{"name": "Release"}]
        self.assertEqual(server._artist_credit_from_list(artists), "")

    def test_non_dict_entries_are_skipped_without_crashing(self):
        artists = ["oops", None, 42, {"name": "Real Artist"}]
        self.assertEqual(server._artist_credit_from_list(artists), "Real Artist")

    def test_missing_name_key_is_skipped(self):
        artists = [{"id": "no-name-here"}, {"name": "Real Artist"}]
        self.assertEqual(server._artist_credit_from_list(artists), "Real Artist")

    def test_whitespace_only_name_is_skipped(self):
        artists = [{"name": "   "}, {"name": "Real Artist"}]
        self.assertEqual(server._artist_credit_from_list(artists), "Real Artist")

    def test_uses_and_not_comma_matching_existing_convention(self):
        # get_radiolist/get_charts_queue/get_radio_queue historically joined
        # with " and "; this helper must not silently switch to a comma and
        # change every existing multi-artist credit's display text.
        artists = [{"name": "A"}, {"name": "B"}, {"name": "C"}]
        self.assertEqual(server._artist_credit_from_list(artists), "A and B and C")

    def test_track_artist_fields_preserve_exact_artist_ids(self):
        artist, entries = server._track_artist_fields({
            "artist": "Real Artist and Guest Artist",
            "artists": [
                {"name": "Real Artist - Topic", "browseId": "UC_REAL"},
                {"name": "Guest Artist", "channelId": "UC_GUEST"},
            ],
        })
        self.assertEqual(artist, "Real Artist and Guest Artist")
        self.assertEqual(entries, [
            {"name": "Real Artist", "id": "UC_REAL"},
            {"name": "Guest Artist", "id": "UC_GUEST"},
        ])

    def test_track_artist_fields_fallback_keeps_single_known_id(self):
        artist, entries = server._track_artist_fields({
            "artist": "Real Artist",
            "artist_id": "UC_REAL",
        })
        self.assertEqual(artist, "Real Artist")
        self.assertEqual(entries, [{"name": "Real Artist", "id": "UC_REAL"}])

    def test_now_playing_snapshot_exposes_structured_artists(self):
        with server._np_lock:
            previous = dict(server._now_playing)
            server._now_playing.update({
                "artist": "Real Artist",
                "artists": [{"name": "Real Artist", "id": "UC_REAL"}],
            })
            try:
                snapshot = server._np_snapshot()
            finally:
                server._now_playing.clear()
                server._now_playing.update(previous)
        self.assertEqual(snapshot["artists"], [{"name": "Real Artist", "id": "UC_REAL"}])


class CleanArtistCreditTests(unittest.TestCase):
    """Direct edge-case coverage of the underlying primitive both the server
    and home_feed helpers build on."""

    def test_none_value(self):
        self.assertEqual(server._clean_artist_credit(None), "")

    def test_non_string_value_coerced(self):
        self.assertEqual(server._clean_artist_credit(123), "123")

    def test_topic_suffix_case_sensitive_exact_match_only(self):
        # ytmusicapi/yt-dlp always emit the exact " - Topic" casing; a
        # differently-cased uploader name coincidentally ending in "topic"
        # should NOT be mistaken for the marker and mangled.
        self.assertEqual(server._clean_artist_credit("Some Band - topic"), "Some Band - topic")

    def test_generic_label_with_surrounding_whitespace(self):
        self.assertEqual(server._clean_artist_credit("  Release  "), "")


if __name__ == "__main__":
    unittest.main()
