"""Regression test for the trailing-slash paste-URL bug.

Bug: when a user pasted a URL like `/album/?browse=ABC` directly into the address
bar, the request bounced through `/login/` and lost the original URL because
Flask's auth bypass keyed on `request.path` against slash-less variants of
`_SPA_DOCUMENT_PATHS` only, the registration loop only registered the slash-less
paths, and `_safe_spa_target` returned the default `/home` for slashed variants.

This test guards every link in that chain so a future refactor cannot silently
re-introduce the bug. Run with ``pytest flask-server/tests/test_routing_paste_url.py`` or
``python -m unittest discover -s flask-server/tests -p 'test_routing_paste_url.py'``.
"""
import os
import sys

# Tests live in flask-server/tests/, while application modules remain in the flask-server parent directory.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
import os
import sys
import types
import unittest
from unittest import mock
from urllib.parse import parse_qs, urlparse

# Stub the heavy native deps BEFORE importing server.py.  The real ytmusicapi
# and alexa_remote packages are not pip-installable in our test environment,
# and the YouTube Browser sidecar obviously cannot run in CI.  server.py does
# `from ytmusicapi import YTMusic` at module top, so these stubs must be in place
# at import time. The env setup writes to ``os.environ`` only on cache miss
# (``setdefault``) so neighbouring test files that exercise the real api_key
# flow keep their own env untouched.

_TEST_ENV = {
    "SECRET_KEY": "test-secret-for-paste-url-regression",
    "REMOTE_USER": "test-owner",
    "REMOTE_PASSWORD": "test-pass",
    # Any non-empty value avoids server.py's auto-generate-and-write-to-disk
    # api_key.txt path, which would litter the working directory on first run.
    "API_KEY": "0123456789abcdef0123456789abcdef",
}


def _install_stubs():
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

    sys.modules["home_feed"] = types.ModuleType("home_feed")

    alexa_remote = types.ModuleType("alexa_remote")
    alexa_remote.AlexaUnreachable = type(
        "AlexaUnreachable", (Exception,), {})
    alexa_remote_remote = types.ModuleType("alexa_remote.remote")
    alexa_remote_remote.devices = lambda refresh=False: (None, "stubbed-no-devices")
    alexa_remote_remote.volume = lambda serial: (None, "stubbed-no-devices")
    alexa_remote_remote.is_logged_in = lambda: (True, None)
    alexa_remote_remote.proxy_start_url = lambda *a, **kw: (None, "stubbed")
    sys.modules["alexa_remote"] = alexa_remote
    sys.modules["alexa_remote.remote"] = alexa_remote_remote

    ybs = types.ModuleType("youtube_browser_session")
    for attr in ("BrowserController", "YouTubeBrowserSessionManager"):
        setattr(ybs, attr, type(attr, (), {"__init__": lambda self, *a, **kw: None}))
    ybs.browser_client_is_signed_in = lambda *a, **kw: False
    ybs.promote_browser_headers = lambda *a, **kw: None
    # Prefer the real module when it is importable (it has no import-time side
    # effects). Overwriting it unconditionally poisoned sys.modules for
    # test_youtube_browser_session.py, which imports the real symbols — a plain
    # `pytest` run over the whole directory then died during collection
    # depending on which test file happened to be imported first.
    if "youtube_browser_session" not in sys.modules:
        try:
            import youtube_browser_session  # noqa: F401
        except Exception:
            sys.modules["youtube_browser_session"] = ybs


_install_stubs()
import server  # noqa: E402 — only place server.py is imported, AFTER stubs


class SpaPathRegistration(unittest.TestCase):
    """_SPA_DOCUMENT_PATHS_ALL is the single source of truth for "this URL is a
    SPA document path" — both the require_api_key bypass AND the registration
    loop must consult the same set."""

    def test_canonical_paths_are_in_all(self):
        for p in ("/home", "/search", "/playlist", "/album", "/artist",
                  "/artist/songs", "/explore", "/library", "/history",
                  "/mood", "/now-playing"):
            self.assertIn(p, server._SPA_DOCUMENT_PATHS_ALL,
                          msg=f"canonical {p} missing from SPA bypass set")

    def test_slash_variants_are_in_all(self):
        for p in ("/home/", "/search/", "/playlist/", "/album/", "/artist/",
                  "/artist/songs/", "/explore/", "/library/", "/mood/",
                  "/now-playing/"):
            self.assertIn(p, server._SPA_DOCUMENT_PATHS_ALL,
                          msg=f"slashed {p} missing from SPA bypass set — "
                              f"this is the original paste-URL bug regressing")

    def test_history_slash_is_excluded(self):
        # /history/ is the JSON history endpoint; it must NOT be bypassed.
        self.assertNotIn("/history/", server._SPA_DOCUMENT_PATHS_ALL,
                         msg="/history/ must NOT be in the SPA bypass set — "
                             "the JSON endpoint there returns JSON, not a shell.")


class SafeSpaTarget(unittest.TestCase):
    """_safe_spa_target must preserve the URL across a login redirect."""

    # Per-SPA-family parametric regression table. Adding a new SPA path
    # with a `_SPA_QUERY_FIELDS` entry is now ONE tuple append below —
    # no new methods needed. Tuple shape:
    #   (canonical_path, full_query_every_allowed_param_present)
    # - `canonical_path`: slash-less normalized form `_safe_spa_target`
    #   produces on success. Must match a key in `_SPA_QUERY_FIELDS`
    #   (otherwise the impl lands in the "default fall" branch and the
    #   parametric test for that family fails loudly).
    # - `full_query`: every allowed `_SPA_QUERY_FIELDS` entry for that
    #   path in canonical order, non-empty. Used to construct the
    #   round-trip input that must be preserved verbatim. The required
    #   param (per `_SPA_QUERY_FIELDS` semantics — first tuple entry)
    #   is implicit in the first key of `full_query`.
    _SAFE_TARGET_FAMILIES = [
        ('/album',         'browse=ABC123'),
        ('/playlist',      'list=PLabc'),
        ('/search',        'q=hello'),
        ('/artist',        'channel=UCabc'),
        ('/artist/songs',  'channel=UCabc'),
        ('/mood',          'params=P&title=T'),
    ]

    @classmethod
    def setUpClass(cls):
        """Defensive guard: every row in `_SAFE_TARGET_FAMILIES` must
        point at a path that has a non-empty `_SPA_QUERY_FIELDS`
        whitelist. Without this guard, a future contributor appending
        e.g. `('/home', '')` would fail confusingly deep inside the
        parametric trio (expected `'/home?'` vs actual `'/home'`,
        because the empty-whitelist branch in `_safe_spa_target`
        skips the `'?' + urlencode(...)` suffix). Running in
        `setUpClass` instead of a regular test method fails the
        entire class on bad table state with a single setup-time error
        instead of producing two related failure lines (this guard
        AND a cascading parametric subTest failure) for one root
        cause."""
        for canonical, _full_query in cls._SAFE_TARGET_FAMILIES:
            whitelist = server._SPA_QUERY_FIELDS.get(canonical, ())
            if not whitelist:
                raise AssertionError(
                    f"_SAFE_TARGET_FAMILIES row {canonical!r} has no "
                    f"_SPA_QUERY_FIELDS whitelist entry. The parametric "
                    f"trio cannot round-trip a path with no allowed query "
                    f"params. Either remove the row or add a named test "
                    f"for that path's empty-whitelist behavior (see e.g. "
                    f"`test_key_on_path_with_empty_whitelist_returns_clean_path`)."
                )

    def test_safe_spa_target_round_trips_for_each_family(self):
        """Parametric regression for the slash/no-slash-plus-param trio
        every SPA path family must satisfy. Collapses what used to be
        nine near-identical one-liner methods (album/playlist/search/
        artist/artist.songs/mood, each in slash + no-slash + required-
        missing variants) into a single test that iterates over
        `_SAFE_TARGET_FAMILIES`. Adding the next SPA path is now one
        tuple append above — no new methods required.

        For each (canonical, full_query) tuple:
        1. Slash + full_query → canonical + full_query (slash-strip +
           query-keep on the slashed input).
        2. No-slash + full_query → canonical + full_query (identical
           output — pins symmetry so subsequent code edits cannot drift
           the two input shapes apart).
        3. Slash + no params → /home (default-fall for missing required).
        4. No-slash + no params → /home (default-fall symmetry).

        A regression in any one branch surfaces as one of these four
        failing instead of hiding behind symmetry between them.
        `pytest -v` reports each `(canonical, full_query)` as a subTest
        bracket so the failing family is named in the failure output."""
        for canonical, full_query in self._SAFE_TARGET_FAMILIES:
            with self.subTest(family=canonical):
                expected = canonical + '?' + full_query
                # Slash variant — exercises the slash-strip branch where
                # the input has a trailing '/'.
                self.assertEqual(
                    server._safe_spa_target(canonical + '/?' + full_query),
                    expected,
                )
                # No-slash variant — same canonical output, pins the
                # symmetric input-shape that downstream browser pastes
                # produce without normalization.
                self.assertEqual(
                    server._safe_spa_target(canonical + '?' + full_query),
                    expected,
                )
                # Slash + missing required param — must fall to default
                # rather than produce a guessable URL.
                self.assertEqual(
                    server._safe_spa_target(canonical + '/'),
                    '/home',
                )
                # No-slash + missing required param — default-fall
                # symmetry.
                self.assertEqual(
                    server._safe_spa_target(canonical),
                    '/home',
                )

    def test_external_url_is_rejected_with_default(self):
        self.assertEqual(server._safe_spa_target("https://evil.example/x"),
                         "/home")

    def test_unknown_path_returns_default(self):
        self.assertEqual(server._safe_spa_target("/totally-fake-path"),
                         "/home")

    def test_history_no_query_normalizes_canonically(self):
        # /history/ normalizes to /history because /history IS in _SPA_DOCUMENT_PATHS.
        self.assertEqual(server._safe_spa_target("/history/"), "/history")

    # ---- `?key=…` (API-key bypass) in the input URL -----------------------
    # `_safe_spa_target` filters the query string through the per-path
    # `_SPA_QUERY_FIELDS` whitelist (q / list / browse / channel / params /
    # title). `key` is intentionally NOT in any whitelist. The contract we
    # pin down here is: a `?key=…` that arrives alongside a valid
    # whitelisted param is silently dropped before the URL survives a login
    # redirect, so the long API key never lands on the login page's
    # `next=` query string, in browser history, or in server logs. A
    # future refactor that widens the whitelist — or adds `key` for
    # convenience — would silently leak the secret into the address bar,
    # so these tests make that mistake loud.
    def test_album_key_paired_with_browse_strips_key_and_keeps_browse(self):
        self.assertEqual(
            server._safe_spa_target("/album/?key=SUPERSECRET&browse=ABC"),
            "/album?browse=ABC",
            msg="API key leaked into login next= target — whitelist regression",
        )

    def test_mood_key_with_two_allowed_params_keeps_both_strips_key(self):
        self.assertEqual(
            server._safe_spa_target("/mood/?key=SUPERSECRET&params=P&title=T"),
            "/mood?params=P&title=T",
            msg="key must be stripped even when both allowed mood params "
                "are present and key appears in the middle of the query",
        )

    def test_any_non_whitelisted_param_including_key_is_dropped(self):
        # Generalises the key-strip promise to every non-whitelisted name:
        # the function must filter through `_SPA_QUERY_FIELDS`, never pass
        # query items through to the redirect target. Catches refactors
        # that switch from a whitelist to a denylist, or that use a
        # `query.items()` loop unguarded.
        self.assertEqual(
            server._safe_spa_target(
                "/album/?browse=ABC&key=SUPERSECRET&evil=redir"),
            "/album?browse=ABC",
            msg="whitelist must drop every non-whitelisted param (key AND "
                "any other garbage), keeping only `_SPA_QUERY_FIELDS` names",
        )

    def test_key_on_path_with_empty_whitelist_returns_clean_path(self):
        # `/home` is a valid SPA path but has `allowed=()` in
        # `_SPA_QUERY_FIELDS` — no required check fires and no whitelist
        # loop runs to filter params. The clean-query branch returns
        # `/home` alone (no `?` appended because `clean` is empty). A
        # naive refactor that builds the returned query from
        # `query.items()` while only skipping `None`-valued entries would
        # silently leak `key` here. Pin down this category.
        self.assertEqual(
            server._safe_spa_target("/home/?key=SUPERSECRET"),
            "/home",
            msg="key on a path with no whitelisted query fields must NOT "
                "leak into the redirect target — bare /home is the safe form",
        )


    # The slash/no-slash-plus-param trio tests that used to live in this
    # section (for `/playlist/`, `/artist/` missing-required, and the
    # search no-slash variant) have been folded into the parametric
    # `_SAFE_TARGET_FAMILIES` table at the top of this class — see
    # `test_safe_spa_target_round_trips_for_each_family`. The named
    # methods below remain because each one pins a distinct code path
    # whose failure reads more honestly under a named assertion than
    # under an `(family=…, branch=…)` subTest bracket.


class FlaskDispatch(unittest.TestCase):
    """End-to-end through the live Flask test client."""

    @classmethod
    def setUpClass(cls):
        # Defensive guard: every (path, _) row in
        # `_FLASK_DISPATCH_FAMILIES` must point at a path that the impl
        # routes to the SPA shell (i.e., is in `_SPA_DOCUMENT_PATHS_ALL`).
        # Without this guard, appending e.g. `('/history', ...,
        # 'logged_out_redirect')` would fail confusingly inside the
        # parametric dispatch loop (since `/history` is intentionally
        # excluded from `_SPA_DOCUMENT_PATHS_ALL` and routes to a JSON
        # 401, not a shell). Surfacing that mistake here gives a single
        # clear setup-time error instead of an opaque 4xx assertion
        # deep in the parametric.
        for path_canon, _q, _s in cls._FLASK_DISPATCH_FAMILIES:
            if path_canon not in server._SPA_DOCUMENT_PATHS_ALL:
                raise AssertionError(
                    f"_FLASK_DISPATCH_FAMILIES row path {path_canon!r} is "
                    f"not in _SPA_DOCUMENT_PATHS_ALL — the parametric "
                    f"dispatch expects every row to point at an SPA path "
                    f"that the impl renders a shell for. If the path is "
                    f"intentionally JSON-only (e.g. /history), keep the "
                    f"named method `test_paste_history_slash_redirects_...` "
                    f"instead of folding it into the parametric."
                )
            # Empty-whitelist trap (parallels SafeSpaTarget's guard): the
            # parametric dispatch always appends `?<query_string>` to the
            # request URL, so a path whose `_SPA_QUERY_FIELDS` whitelist is
            # empty would have its query string silently stripped by
            # `_safe_spa_target` in the round-trip assertion (target
            # collapses to `_safe_spa_target(<path>)` == `/home`
            # regardless of query). Such a row is operationally useless
            # in the paste-URL regression flow because the next= param
            # would not carry any evidence of the original query.
            if not server._SPA_QUERY_FIELDS.get(path_canon, ()):
                raise AssertionError(
                    f"_FLASK_DISPATCH_FAMILIES row path {path_canon!r} "
                    f"has empty `_SPA_QUERY_FIELDS` whitelist — the "
                    f"parametric dispatch relies on `_safe_spa_target` "
                    f"preserving the query string in the redirect "
                    f"`next=`, which only works for paths with a non-empty "
                    f"whitelist. Either remove the row, add a "
                    f"`_SPA_QUERY_FIELDS` entry for that path, or move "
                    f"the row to a named test (see e.g. "
                    f"`test_key_on_path_with_empty_whitelist_returns_clean_path`)."
                )
        cls.client = server.app.test_client()

    # Per-family parametric regression table covering the two paste-URL
    # scenarios each SPA path family must satisfy: logged-out owner pastes
    # the URL → redirected to /login/?next=… with the original URL
    # preserved in `next=`; logged-in owner pastes the URL → renders
    # the SPA shell. Adding a new SPA path with a `_SPA_QUERY_FIELDS`
    # entry is one tuple append below — no new methods needed. Each
    # row is expanded inside the parametric test to BOTH slash forms
    # (`<path>/` and `<path>`) so that a single tuple exercises both
    # `strict_slashes=False` registrations.
    #
    # Tuple shape: (path_canonical_without_trailing_slash,
    #               query_string_already_amp_urlencoded,
    #               scenario)
    # where `scenario in {"logged_out_redirect", "logged_in_render"}`.
    # An unknown scenario fails loudly inside the parametric loop via
    # `self.fail(...)` so a typo bubbles up fast.
    _FLASK_DISPATCH_FAMILIES = [
        ('/album',         'browse=ABC123',       'logged_out_redirect'),
        ('/album',         'browse=ABC123',       'logged_in_render'),
        ('/search',        'q=hello',             'logged_out_redirect'),
        ('/search',        'q=hello',             'logged_in_render'),
        ('/playlist',      'list=PLabc123',       'logged_out_redirect'),
        ('/artist',        'channel=UCabc',       'logged_out_redirect'),
        ('/artist/songs',  'channel=UC123',       'logged_out_redirect'),
        ('/mood',          'params=P&title=T',    'logged_out_redirect'),
    ]

    def _expect_login_redirect_with_next(self, path, query):
        """Owner who is NOT logged in — pasting the URL must redirect to
        /login/?next=… with the ORIGINAL URL preserved in `next=`.
        Before the fix the next param collapsed to /home, hiding the bug.
        Used by the parametric `logged_out_redirect` scenario rows and by
        the `/history/` named special."""
        response = self.client.get(path + ("?" + query if query else ""))
        self.assertEqual(response.status_code, 302,
                         msg=f"paste {path}?{query} expected 302, got {response.status_code}")
        location = response.headers.get("Location", "")
        parsed = urlparse(location)
        self.assertEqual(parsed.path, "/login/")
        qs = parse_qs(parsed.query)
        self.assertIn("next", qs, msg=f"no next param on {location}")
        self.assertNotEqual(qs["next"][0], "/home",
                            msg=f"next defaults to /home — original URL lost: "
                                f"{location}")
        self.assertEqual(
            qs["next"][0],
            server._safe_spa_target(path + ("?" + query if query else "")),
        )

    def test_dispatch_round_trips_for_each_family(self):
        """Parametric dispatcher regression: every (path, query, scenario)
        row in `_FLASK_DISPATCH_FAMILIES` is exercised in BOTH slash
        forms so a single tuple covers both `strict_slashes=False`
        registrations. Each row produces two `subTest` brackets
        (slash + no-slash). Adding the next paste-URL coverage = one
        tuple append above."""
        for path_canon, query_string, scenario in self._FLASK_DISPATCH_FAMILIES:
            for slash_form in (path_canon + '/', path_canon):
                with self.subTest(family=path_canon, slash=slash_form,
                                  scenario=scenario):
                    if scenario == 'logged_out_redirect':
                        self._expect_login_redirect_with_next(slash_form,
                                                              query_string)
                    elif scenario == 'logged_in_render':
                        with mock.patch.object(server, '_logged_in',
                                                return_value=True), \
                             mock.patch.object(server, '_jam_guest',
                                                return_value=False):
                            response = self.client.get(
                                slash_form + '?' + query_string)
                            self.assertEqual(
                                response.status_code, 200,
                                msg=f"expected 200 shell render for "
                                    f"{slash_form}?{query_string}, got "
                                    f"{response.status_code}")
                            ct = response.headers.get("Content-Type", "")
                            self.assertTrue(
                                ct.startswith("text/html"),
                                msg=f"expected text/html shell, got "
                                    f"Content-Type={ct!r}")
                    else:
                        # Loud failure: an unknown scenario value almost
                        # certainly means a typo or a copy-paste mistake.
                        self.fail(
                            f"unknown scenario {scenario!r} in row "
                            f"({path_canon!r}, {query_string!r})")

    # Special case: /history/ is intentionally NOT in the parametric
    # table because the JSON endpoint there has a different contract
    # (no `next=` preservation on auth failure), and routes to JSON
    # 401 instead of an HTML shell. Keep this as a named method so the
    # contract stays explicit and reviewable.
    def test_paste_history_slash_redirects_to_login_without_history_next(self):
        response = self.client.get("/history/")
        self.assertIn(response.status_code, (302, 401))
        if response.status_code == 302:
            location = response.headers.get("Location", "")
            parsed = urlparse(location)
            self.assertEqual(parsed.path, "/login/")
            qs = parse_qs(parsed.query)
            self.assertNotIn("next", qs)


class LibraryPlaylistEndpoint(unittest.TestCase):
    """Regression for: GET /api/library/playlists/<id> with a non-playlist id
    (album browse_id `MPREb_*`, deleted id, garbage string) must return
    JSON 404 ``{"error": {"code": "not_a_playlist"}}`` — not a generic 500.

    Bug: the upstream ytmusicapi rejects ids that aren't playlists with
    ``Invalid id`` / ``Not found`` (and a couple of similar phrasings). The
    handler used to bubble those out as ``return jsonify({'error': str(e)}),
    500``, which lit up the browser console with a red `Failed to load
    resource` line for every album row that the explore / home shelves
    mis-routed through the playlist fetcher. The fix maps those upstream
    miss errors to a clean 404 so existing client-side 404-fallbacks (and
    the new `_looksLikeAlbumBrowseId` shortcut in preload-nav.js) can
    route the call to the right endpoint."""

    def _fake_ytmusic(self, *, get_playlist_response=None,
                      get_playlist_error=None,
                      get_watch_playlist_error=None):
        """Build a stub YTMusic with predictable behaviour for one request.
        ``get_playlist`` either returns the supplied dict or raises the
        supplied exception. ``get_watch_playlist`` always raises by default
        to exercise the fallback chain; pass an explicit override for happy
        path tests."""
        fake = mock.Mock()
        fake.auth_type = 'BROWSER'
        if get_playlist_error is not None:
            fake.get_playlist.side_effect = get_playlist_error
        else:
            fake.get_playlist.return_value = get_playlist_response or {
                'title': '', 'trackCount': 0, 'tracks': [],
            }
        fake.get_watch_playlist.side_effect = (
            get_watch_playlist_error
            if get_watch_playlist_error is not None
            else Exception('Playlist not found (404)')
        )
        return fake

    def test_album_browse_id_returns_404_not_500(self):
        # Repro of the exact URL the user reported in the bug:
        # /api/library/playlists/MPREb_OqFjF74lTQu?offset=0&limit=30
        # upstream ytmusicapi raises with "Invalid id"-shaped text.
        fake = self._fake_ytmusic(
            get_playlist_error=Exception(
                'Invalid id (404): MPREb_OqFjF74lTQu'))
        with mock.patch.object(server, '_logged_in', return_value=True), \
             mock.patch.object(server, '_jam_guest', return_value=False), \
             mock.patch.object(server, '_get_ytmusic_home',
                               return_value=fake):
            response = self.client.get(
                '/api/library/playlists/MPREb_OqFjF74lTQu?offset=0&limit=30')
        self.assertNotEqual(
            response.status_code, 500,
            msg=f'wrong-id request leaked as 500 (the original bug): '
                f'body={response.get_data(as_text=True)!r}')
        self.assertEqual(response.status_code, 404)
        body = response.get_json()
        self.assertIsInstance(body, dict)
        self.assertEqual(body.get('error', {}).get('code'), 'not_a_playlist',
                         msg=f'expected {{error.code = not_a_playlist}}; '
                             f'got body={body!r}')
        # The leaked exception string must not appear in the response —
        # it can carry ytmusicapi internals we don't want in the console.
        self.assertNotIn('Invalid id', response.get_data(as_text=True),
                         msg='ytmusicapi internal error text leaked to client')

    def test_upstream_miss_phrases_all_return_404(self):
        # Pin every ytmusicapi phrase the fix maps to 404. Garbage /
        # deleted / wrong-type ids all surface with one of these messages;
        # a future ytmusicapi phrasing change should break this parametric
        # here (loud) rather than as a silent regression to 500.
        # The pl_id sent in is the SAME for every row — the upstream
        # error message is what's under test, not the id shape. Using a
        # distinct id per row would let two phrases that share a prefix
        # (e.g. 'Playlist not found' and 'Playlist does not exist' both
        # start with 'Playlist') silently hit the same URL twice, hiding
        # any per-url caching/state we'd want to catch later.
        sentinel_id = 'uphrases404_xyz'
        for msg in ('invalid argument: bogus id',
                    'Playlist not found',
                    'Invalid id supplied',
                    'Playlist does not exist'):
            with self.subTest(msg=msg):
                fake = self._fake_ytmusic(
                    get_playlist_error=Exception(msg))
                with mock.patch.object(server, '_logged_in',
                                       return_value=True), \
                     mock.patch.object(server, '_jam_guest',
                                       return_value=False), \
                     mock.patch.object(server, '_get_ytmusic_home',
                                       return_value=fake):
                    response = self.client.get(
                        f'/api/library/playlists/{sentinel_id}')
                self.assertEqual(
                    response.status_code, 404,
                    msg=f'"{msg}" should map to 404, got '
                        f'{response.status_code}; body='
                        f'{response.get_data(as_text=True)!r}')
                self.assertEqual(
                    response.get_json().get('error', {}).get('code'),
                    'not_a_playlist')

    def test_valid_playlist_id_routes_to_get_playlist(self):
        # Regression: a real PL id must still reach get_playlist and return
        # 200 with the page_response-shaped body.
        fake = self._fake_ytmusic(get_playlist_response={
            'title': 'My Mix', 'trackCount': 2,
            'tracks': [
                {'videoId': 'aaa', 'title': 'A',
                 'artists': [{'name': 'AA'}],
                 'thumbnails': [], 'duration': '3:00',
                 'duration_seconds': 180},
                {'videoId': 'bbb', 'title': 'B',
                 'artists': [{'name': 'BB'}],
                 'thumbnails': [], 'duration': '4:00',
                 'duration_seconds': 240},
            ],
        })
        with mock.patch.object(server, '_logged_in', return_value=True), \
             mock.patch.object(server, '_jam_guest', return_value=False), \
             mock.patch.object(server, '_get_ytmusic_home',
                               return_value=fake):
            response = self.client.get(
                '/api/library/playlists/PLabc123?offset=0&limit=30')
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body.get('title'), 'My Mix')
        self.assertEqual(body.get('trackCount'), 2)
        self.assertEqual(len(body.get('tracks') or []), 2)
        # The handler's `fetch_limit = offset + limit + 1` adds 1 so the
        # client can detect has_more on the next page. Pin the id argument
        # exactly and verify the fetch_limit via mock.ANY + a follow-up
        # invariant check so a future padding change is a name-not-a-silent
        # test failure.
        self.assertEqual(fake.get_playlist.call_count, 1)
        args, _kw = fake.get_playlist.call_args
        self.assertEqual(args[0], 'PLabc123')
        self.assertEqual(args[1], 31,
                         msg=f'expected fetch_limit = offset(0) + '
                             f'limit(30) + 1 has_more padding; got {args[1]}')

    def test_unexpected_upstream_failure_returns_sanitised_502(self):
        # Round-4 sanitisation: an unrecognised upstream error (e.g. an
        # HTTP transport-level failure like "connection refused") must
        # NOT leak the raw exception string into the response body.
        # ytmusicapi phrasing is volatile; surfacing internal httpx /
        # urllib library text in the browser console exposes internal
        # class names that we don't want in client logs. The contract
        # is now 502 bad_gateway + a sanitised message that mentions
        # only the request-injected pl_id (for support correlation).
        fake = self._fake_ytmusic(
            get_playlist_error=Exception(
                'HTTPSConnectionPool: connection refused'))
        with mock.patch.object(server, '_logged_in', return_value=True), \
             mock.patch.object(server, '_jam_guest', return_value=False), \
             mock.patch.object(server, '_get_ytmusic_home',
                               return_value=fake):
            response = self.client.get('/api/library/playlists/PLabc123')
        self.assertEqual(response.status_code, 502,
                         msg='unrecognised upstream error must return 502, '
                             f'not 500-leak: body={response.get_data(as_text=True)!r}')
        body = response.get_json()
        self.assertEqual(body.get('error', {}).get('code'), 'bad_gateway',
                         msg='unclassified upstream error must use bad_gateway code')
        body_text = response.get_data(as_text=True)
        # Substring-leak guards: the upstream transport error text
        # (httpx class name + diagnostic phrase) must NOT appear in the
        # body. The pl_id IS allowed because it is the request-supplied
        # correlation key the support surface relies on.
        self.assertNotIn('HTTPSConnectionPool', body_text,
                         msg='httpx internal class name leaked into response body')
        self.assertNotIn('connection refused', body_text,
                         msg='transport diagnostic phrase leaked into response body')

    def test_bad_request_phrase_is_sanitised_502_not_404(self):
        # Round-5 regression pin for the deliberate narrowing:
        # 'bad request' was dropped from the not-found classifier
        # because it substring-matched too many unrelated validation
        # errors elsewhere in the request lifecycle. The trade-off is
        # that a ytmusicapi phrase (e.g. 'bad request: playlist fetch
        # failed') whose ONLY distinguishing match against the legacy
        # classifier was 'bad request' now lands on the sanitised-502
        # branch instead of 404. Pin that here so a future contributor
        # who re-adds 'bad request' to the not-found needles sees this
        # test break and reads the trade-off comment in server.py
        # before proceeding.
        # NOTE on phrase choice: the simulated message must NOT
        # substring-match any remaining not-found needle
        # ('invalid argument', 'invalid id', 'playlist not found',
        # 'playlist does not exist', 'no such playlist', 'http 400',
        # 'cannot find playlist', 'not a valid playlist', 'unsupported
        # playlist') — otherwise the test would still hit 404 under
        # the new contract and the pin would not exercise the
        # dropped-'bad request' trade-off. The chosen phrase
        # 'bad request: playlist fetch failed' contains the dropped
        # needle and nothing else from any current needle list. A
        # distinct pl_id 'sanitised_bad_request_x' is used to keep
        # the substring-leak guard below able to look for the
        # distinctive substring 'playlist fetch failed' without
        # false-positives from the pl_id itself.
        secret = 'bad request: playlist fetch failed'
        fake = self._fake_ytmusic(
            get_playlist_error=Exception(secret))
        with mock.patch.object(server, '_logged_in', return_value=True), \
             mock.patch.object(server, '_jam_guest', return_value=False), \
             mock.patch.object(server, '_get_ytmusic_home',
                               return_value=fake):
            response = self.client.get('/api/library/playlists/sanitised_bad_request_x')
        self.assertEqual(
            response.status_code, 502,
            msg=f'"bad request" must now go to sanitised 502, not 404; '
                f'got {response.status_code}: '
                f'{response.get_data(as_text=True)!r}')
        body = response.get_json()
        self.assertEqual(body.get('error', {}).get('code'),
                         'bad_gateway',
                         msg='broad "bad request" must use bad_gateway code')
        body_text = response.get_data(as_text=True)
        # The simulated upstream phrase must NOT appear in the body
        # — only the request-supplied pl_id is allowed since it's
        # the support-correlation key. 'playlist fetch failed' is the
        # distinctive substring of the simulated ytmusicapi
        # exception; the pl_id 'sanitised_bad_request_x' redundantly
        # contains 'bad request' as noise but NOT 'playlist fetch
        # failed', so the guard below catches a real leak.
        self.assertNotIn('playlist fetch failed', body_text,
                         msg='upstream ytmusicapi exception text leaked '
                             'into response — only pl_id / status code are allowed')

    def test_loose_not_found_phrase_remains_sanitised_502(self):
        # Round-4 hardening: an unrecognised upstream error must NEVER
        # leak str(e) into the response body. ytmusicapi phrasing changes
        # over time and a stale needle list must not surface a raw
        # exception string in the browser console. The contract is now
        # 502 (upstream) with the request id / class name stripped —
        # NOT 500 with str(e).
        leaked_phrase = "YTMusicError: some-new-phrase (500) that wraps private state"
        fake = self._fake_ytmusic(
            get_playlist_error=Exception(leaked_phrase))
        with mock.patch.object(server, '_logged_in', return_value=True), \
             mock.patch.object(server, '_jam_guest', return_value=False), \
             mock.patch.object(server, '_get_ytmusic_home',
                               return_value=fake):
            response = self.client.get('/api/library/playlists/PLabc123')
        self.assertEqual(response.status_code, 502,
                         msg='unrecognised upstream error must return 502, '
                             f'not 500. body={response.get_data(as_text=True)!r}')
        body_text = response.get_data(as_text=True)
        self.assertNotIn('YTMusicError', body_text,
                         msg='ytmusicapi class name leaked into response body')
        self.assertNotIn('some-new-phrase', body_text,
                         msg='ytmusicapi exception text leaked into response body')
        self.assertEqual(
            response.get_json().get('error', {}).get('code'),
            'bad_gateway',
            msg='unclassified upstream error must use the bad_gateway code')

    def test_ytmusicapi_auth_phrase_returns_401(self):
        # Auth-shaped ytmusicapi errors map cleanly to JSON 401 so the
        # SPA's auth gate can prompt re-auth instead of a useless 500
        # or 502 from upstream. Pin the contract.
        for phrase in ('401 Unauthorized', 'authentication required',
                       'invalid credentials', 'Please sign in to continue'):
            with self.subTest(phrase=phrase):
                fake = self._fake_ytmusic(
                    get_playlist_error=Exception(phrase))
                with mock.patch.object(server, '_logged_in',
                                       return_value=True), \
                     mock.patch.object(server, '_jam_guest',
                                       return_value=False), \
                     mock.patch.object(server, '_get_ytmusic_home',
                                       return_value=fake):
                    response = self.client.get('/api/library/playlists/PLabc')
                self.assertEqual(
                    response.status_code, 401,
                    msg=f'"{phrase}" should map to 401, got '
                        f'{response.status_code}')
                self.assertEqual(
                    response.get_json().get('error', {}).get('code'),
                    'unauthorized')

    def test_loose_not_found_phrase_remains_sanitised_now(self):
        # Round-4 regression for the round-1 needle-narrowing concern: a
        # payload-shape error mentioning 'not found' but unrelated to the
        # playlist id (e.g. a parse error in a fetcher chain) must STILL
        # be sanitised — no str(e) leak. The pre-round-4 contract would
        # have been a 500; the post-round-4 contract is a 502 with no
        # raw exception text in the body.
        fake = self._fake_ytmusic(
            get_playlist_error=Exception(
                "could not parse response: 'Library state' not found in payload"))
        with mock.patch.object(server, '_logged_in', return_value=True), \
             mock.patch.object(server, '_jam_guest', return_value=False), \
             mock.patch.object(server, '_get_ytmusic_home',
                               return_value=fake):
            response = self.client.get('/api/library/playlists/PLabc123')
        self.assertEqual(response.status_code, 502,
                         msg='unrelated "not found" payload error must '
                             'go to sanitised 502, not 500-leak')
        body_text = response.get_data(as_text=True)
        self.assertNotIn('Library state', body_text,
                         msg='plain "not found" payload text leaked into body')


if __name__ == "__main__":
    unittest.main()
