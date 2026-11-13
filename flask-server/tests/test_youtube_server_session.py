"""Server credentials authorize downloads only; account calls stay private."""
import os
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))


def _install_stubs():
    os.environ.setdefault("SECRET_KEY", "test-secret-for-server-session")
    os.environ.setdefault("REMOTE_USER", "test-owner")
    os.environ.setdefault("REMOTE_PASSWORD", "test-pass")
    os.environ.setdefault("API_KEY", "0123456789abcdef0123456789abcdef")  # gitleaks:allow -- synthetic fixture credential

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

    sys.modules.setdefault("home_feed", types.ModuleType("home_feed"))

    if "alexa_remote" not in sys.modules:
        alexa_remote = types.ModuleType("alexa_remote")
        alexa_remote.AlexaUnreachable = type("AlexaUnreachable", (Exception,), {})
        alexa_remote_remote = types.ModuleType("alexa_remote.remote")
        alexa_remote_remote.devices = lambda refresh=False: (None, "stubbed-no-devices")
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
            ybs.normalize_browser_headers = lambda value: value
            sys.modules["youtube_browser_session"] = ybs


_install_stubs()
import server  # noqa: E402
from youtube_credentials import YouTubeCredentialStore, OWNER, SERVER  # noqa: E402

UUID = "0f0e0d0c-0b0a-4908-8706-050403020100"
ACCOUNT = "user:" + UUID


def netscape_jar(domain=".youtube.com", expires=None):
    expiry = int(expires if expires is not None else time.time() + 3600)
    return ("# Netscape HTTP Cookie File\n"
            f"{domain}\tTRUE\t/\tFALSE\t{expiry}\tSID\tsigned-in-cookie\n")


class _TempStore(unittest.TestCase):
    """A credential store rooted in a temp dir, with the owner file beside it."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = self.tmp.name
        self.owner_path = os.path.join(self.dir, "headers_auth.json")
        store = YouTubeCredentialStore(self.dir, lambda: self.owner_path)
        patcher = mock.patch.object(server, "_youtube_credentials", store)
        patcher.start()
        self.addCleanup(patcher.stop)

    def touch(self, identity):
        if identity == OWNER:
            path = self.owner_path
        elif identity == SERVER:
            path = os.path.join(self.dir, "server.json")
        else:
            path = os.path.join(self.dir, identity.split(":", 1)[1] + ".json")
        with open(path, "w", encoding="utf-8") as stream:
            stream.write('{"cookie": "x"}')
        return path


class FallbackSessionTests(_TempStore):
    def test_a_linked_account_reads_its_own_credential(self):
        own = self.touch(ACCOUNT)
        self.touch(SERVER)
        self.assertEqual(server._yt_auth_file(ACCOUNT), own)

    def test_an_unlinked_account_never_borrows_the_server_credential(self):
        borrowed = self.touch(SERVER)
        self.assertIsNone(server._yt_auth_file(ACCOUNT))

    def test_the_switch_turns_the_borrowed_session_off(self):
        self.touch(SERVER)
        with mock.patch.dict(os.environ, {"YOUTUBE_SERVER_SESSION": "0"}):
            self.assertIsNone(server._yt_auth_file(ACCOUNT))

    def test_the_server_identity_never_falls_back_to_itself(self):
        # server.json is missing precisely when the keepalive needs to sign in;
        # returning its own (absent) path would hide that from the check.
        self.assertIsNone(server._yt_auth_file(SERVER))

    def test_an_unlinked_owner_cannot_borrow_the_server_session(self):
        # The fallback is about "has no session", not about the account's name: a
        # legacy owner file that was never captured is as unlinked as any account.
        borrowed = self.touch(SERVER)
        self.assertIsNone(server._yt_auth_file(OWNER))

    def test_the_owners_own_session_still_wins(self):
        own = self.touch(OWNER)
        self.touch(SERVER)
        self.assertEqual(server._yt_auth_file(OWNER), own)


class DownloadCookieFallbackTests(_TempStore):
    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(server.download_cookies, "saved_path",
                                    return_value=__import__("pathlib").Path(self.dir) /
                                    "download_cookies.txt")
        patcher.start()
        self.addCleanup(patcher.stop)

    def write(self, name):
        path = os.path.join(self.dir, name)
        with open(path, "w", encoding="utf-8") as stream:
            stream.write(netscape_jar())
        return path

    def test_an_account_without_its_own_jar_downloads_with_the_servers(self):
        borrowed = self.write("download_cookies_server.txt")
        self.assertEqual(server.Supporting.get_ytdlp_cookies_file(ACCOUNT), borrowed)

    def test_the_shared_server_jar_wins_over_an_accounts_own_jar(self):
        shared = self.write("download_cookies_server.txt")
        self.write("download_cookies_%s.txt" % UUID)
        self.assertEqual(server.Supporting.get_ytdlp_cookies_file(ACCOUNT), shared)

    def test_an_accounts_jar_covers_an_unusable_server_jar(self):
        with open(os.path.join(self.dir, "download_cookies_server.txt"), "w",
                  encoding="utf-8") as stream:
            stream.write("")
        own = self.write("download_cookies_%s.txt" % UUID)
        self.assertEqual(server.Supporting.get_ytdlp_cookies_file(ACCOUNT), own)

    def test_the_switch_turns_the_shared_jar_off(self):
        self.write("download_cookies_server.txt")
        with mock.patch.dict(os.environ, {"YOUTUBE_SERVER_SESSION": "0"}):
            self.assertIsNone(server.Supporting.get_ytdlp_cookies_file(ACCOUNT))

    def test_the_switch_falls_back_to_an_accounts_own_jar(self):
        self.write("download_cookies_server.txt")
        own = self.write("download_cookies_%s.txt" % UUID)
        with mock.patch.dict(os.environ, {"YOUTUBE_SERVER_SESSION": "0"}):
            self.assertEqual(server.Supporting.get_ytdlp_cookies_file(ACCOUNT), own)


class RenewalTargetTests(_TempStore):
    def test_a_borrower_renews_the_session_it_reads(self):
        self.assertEqual(server._youtube_renewal_identity(ACCOUNT), ACCOUNT)

    def test_a_linked_account_renews_its_own_profile(self):
        self.touch(ACCOUNT)
        self.assertEqual(server._youtube_renewal_identity(ACCOUNT), ACCOUNT)

    def test_with_the_fallback_off_nothing_is_redirected(self):
        with mock.patch.dict(os.environ, {"YOUTUBE_SERVER_SESSION": "no"}):
            self.assertEqual(server._youtube_renewal_identity(ACCOUNT), ACCOUNT)

    def test_a_borrower_queues_one_automated_renewal_when_the_reload_fails(self):
        with mock.patch.object(server._youtube_browser_sessions,
                               "call_with_one_refresh",
                               side_effect=RuntimeError("dead")) as refresh, \
                mock.patch.object(server, "_request_server_session_renewal") as queued:
            with self.assertRaises(RuntimeError):
                server._with_youtube_auth_renewal(lambda: None, ACCOUNT)
        refresh.assert_called_once()
        self.assertEqual(refresh.call_args.args[1], ACCOUNT)
        queued.assert_not_called()

    def test_a_linked_account_failure_does_not_queue_a_server_renewal(self):
        self.touch(ACCOUNT)
        with mock.patch.object(server._youtube_browser_sessions,
                               "call_with_one_refresh",
                               side_effect=RuntimeError("dead")) as refresh, \
                mock.patch.object(server, "_request_server_session_renewal") as queued:
            with self.assertRaises(RuntimeError):
                server._with_youtube_auth_renewal(lambda: None, ACCOUNT)
        self.assertEqual(refresh.call_args.args[1], ACCOUNT)
        queued.assert_not_called()


class FakeLogin:
    def __init__(self, configured=True, enabled=True, due=True):
        self.account = types.SimpleNamespace(configured=configured,
                                             email="server@example.com")
        self.enabled = enabled
        self._due = due
        self.log = types.SimpleNamespace(due=lambda: self._due)
        self.calls = []

    def sign_in(self, profile_key="", reason="scheduled"):
        self.calls.append((profile_key, reason))
        return {"outcome": "signed_in", "ok": True, "detail": ""}


class KeepaliveDecisionTests(unittest.TestCase):
    def setUp(self):
        self.login = FakeLogin()

    def tick(self, authenticated=False):
        with mock.patch.object(server, "_youtube_server_login", self.login), \
                mock.patch.object(server, "_ytmusic_is_authenticated",
                                  return_value=authenticated):
            return server._youtube_server_session_tick()

    def test_a_working_session_costs_nothing(self):
        self.assertIsNone(self.tick(authenticated=True))
        self.assertEqual(self.login.calls, [])

    def test_a_dead_session_is_renewed_as_the_server_account(self):
        result = self.tick(authenticated=False)
        self.assertTrue(result["ok"])
        self.assertEqual(self.login.calls, [(SERVER, "scheduled")])

    def test_backoff_is_respected(self):
        self.login._due = False
        self.assertIsNone(self.tick(authenticated=False))
        self.assertEqual(self.login.calls, [])

    def test_an_unconfigured_account_is_not_an_outage(self):
        self.login.account.configured = False
        self.assertIsNone(self.tick(authenticated=False))
        self.assertEqual(self.login.calls, [])

    def test_a_switched_off_login_does_not_run(self):
        self.login.enabled = False
        self.assertIsNone(self.tick(authenticated=False))
        self.assertEqual(self.login.calls, [])

    def test_the_session_switch_stops_the_keepalive_too(self):
        with mock.patch.dict(os.environ, {"YOUTUBE_SERVER_SESSION": "off"}):
            self.assertIsNone(self.tick(authenticated=False))
        self.assertEqual(self.login.calls, [])


class KeepaliveThreadTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(server, "_youtube_keepalive_thread", None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_keepalive_starts_once_per_process(self):
        release = threading.Event()
        seen = []

        def loop():
            seen.append(threading.current_thread().name)
            release.wait(5)

        with mock.patch.object(server, "_youtube_server_session_loop", loop):
            first = server.start_youtube_server_keepalive()
            second = server.start_youtube_server_keepalive()
            self.assertIs(first, second)
            release.set()
            first.join(5)
        self.assertEqual(len(seen), 1)
        self.assertEqual(first.name, "youtube-server-keepalive")


class SaveServerSessionTests(_TempStore):
    def setUp(self):
        super().setUp()
        self.server_json = os.path.join(self.dir, "server.json")
        self.jar = os.path.join(self.dir, "download_cookies_server.txt")
        patcher = mock.patch.object(server.download_cookies, "saved_path",
                                    return_value=__import__("pathlib").Path(self.dir) /
                                    "download_cookies.txt")
        patcher.start()
        self.addCleanup(patcher.stop)

    def save(self, cookies):
        def promote(headers, auth_file, setup_browser, factory):
            with open(auth_file, "w", encoding="utf-8") as stream:
                stream.write("{}")

        with mock.patch.object(server, "promote_browser_headers", promote), \
                mock.patch.object(server, "_invalidate_youtube_auth_caches") as invalidated, \
                mock.patch.object(server, "_remember_youtube_session") as remembered:
            server._save_server_session({"cookie": "x"}, cookies)
        return invalidated, remembered

    def test_a_captured_login_becomes_the_shared_credential(self):
        invalidated, remembered = self.save(netscape_jar())
        self.assertTrue(os.path.isfile(self.server_json))
        self.assertTrue(os.path.isfile(self.jar))
        self.assertEqual(os.stat(self.jar).st_mode & 0o777, 0o600)
        invalidated.assert_called_once_with(SERVER)
        # The server's login is nobody's linked account: no encrypted row.
        remembered.assert_not_called()

    def test_a_capture_without_cookies_keeps_the_old_jar(self):
        with open(self.jar, "w", encoding="utf-8") as stream:
            stream.write(netscape_jar())
        before = os.stat(self.jar).st_mtime_ns
        self.save("")
        self.assertEqual(os.stat(self.jar).st_mtime_ns, before)

    def test_a_jar_without_youtube_cookies_never_replaces_a_good_one(self):
        with open(self.jar, "w", encoding="utf-8") as stream:
            stream.write(netscape_jar())
        with open(self.jar, encoding="utf-8") as stream:
            before = stream.read()
        with self.assertRaises(ValueError):
            self.save(netscape_jar(domain=".example.com"))
        with open(self.jar, encoding="utf-8") as stream:
            self.assertEqual(stream.read(), before)


ADMIN_ROW = "admin-row-uuid"
ADMIN = {"id": ADMIN_ROW, "role": "owner", "status": "active", "username": "owner"}
TARGET = {"id": UUID, "role": "user", "status": "active", "username": "sanam"}


class BrowserAsTokenTests(unittest.TestCase):
    """The signed `?as=` token: narrow, replay-proof and revocable."""

    def setUp(self):
        self.layer = types.SimpleNamespace(
            accounts=types.SimpleNamespace(get_user=lambda user_id: TARGET
                                           if str(user_id) == UUID else None))
        for patch in (
            mock.patch.object(server, "_multiuser_layer", self.layer),
            mock.patch.object(server, "_owner_row_id", return_value=ADMIN_ROW),
            mock.patch.object(server, "_mu_web_identity", return_value=ADMIN),
        ):
            patch.start()
            self.addCleanup(patch.stop)
        token = server._PLAYER_IDENTITY.set("owner")
        self.addCleanup(server._PLAYER_IDENTITY.reset, token)

    def target(self, query):
        with server.app.test_request_context("/api/youtube/browser-session/status" + query):
            return server._browser_as_target()

    def token(self, admin="owner", target=ACCOUNT):
        from urllib.parse import quote
        return quote(server.issue_browser_as_token(admin, target))

    def test_a_token_minted_for_this_admin_reaches_the_account(self):
        self.assertEqual(self.target("?as=" + self.token()), ACCOUNT)

    def test_no_token_means_no_impersonation(self):
        self.assertIsNone(self.target(""))

    def test_a_forged_token_is_rejected(self):
        self.assertIsNone(self.target("?as=not-a-token"))

    def test_a_token_for_another_admin_does_not_work_here(self):
        self.assertIsNone(self.target("?as=" + self.token(admin="user:%s" % UUID)))

    def test_the_owner_slot_cannot_be_targeted(self):
        self.assertIsNone(self.target("?as=" + self.token(target="owner")))

    def test_the_signed_in_actor_must_be_the_admin_in_the_token(self):
        with mock.patch.object(server, "_mu_web_identity",
                               return_value={"id": UUID, "role": "admin", "status": "active"}):
            self.assertIsNone(self.target("?as=" + self.token()))
        with mock.patch.object(server, "_mu_web_identity", return_value=None):
            self.assertIsNone(self.target("?as=" + self.token()))

    def test_an_admin_without_authority_over_the_account_is_refused(self):
        target = dict(TARGET, role="owner")
        self.layer.accounts.get_user = lambda user_id: target
        with mock.patch.object(
                server, "_mu_web_identity",
                return_value={"id": ADMIN_ROW, "role": "admin", "status": "active"}):
            self.assertIsNone(self.target("?as=" + self.token()))

    def test_the_target_identity_must_be_the_current_mapping(self):
        with mock.patch.object(server, "_player_identity_for_user_id",
                               return_value="owner"):
            self.assertIsNone(self.target("?as=" + self.token()))

    def test_the_browser_owner_id_uses_the_target_when_authorised(self):
        with server.app.test_request_context(
                "/api/youtube/browser-session/status?as=" + self.token()):
            self.assertEqual(server._browser_owner_id(), ACCOUNT)

    def test_without_a_token_the_browser_owner_id_is_the_caller(self):
        with server.app.test_request_context("/api/youtube/browser-session/status"):
            self.assertEqual(server._browser_owner_id(), "owner")


class FailureAlertTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = mock.patch.dict(os.environ, {
            "MULTIUSER_MAIL_OUTBOX": self.tmp.name,
            "LOGIN_FAIL_MAIL_ID": "alerts@example.com",
            "MULTIUSER_APP_NAME": "Music Box",
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        for key in ("MULTIUSER_SMTP_HOST", "MULTIUSER_MAIL_REDIRECT_TO",
                    "MULTIUSER_MAIL_SUPPRESS_SEND"):
            os.environ.pop(key, None)
        patcher = mock.patch.object(server, "_youtube_server_login", FakeLogin())
        patcher.start()
        self.addCleanup(patcher.stop)

    def alert(self, outcome="wrong_password", detail="Google says the password is wrong."):
        server._send_youtube_login_alert(
            outcome, detail, "scheduled",
            {"consecutive_failures": 3,
             "next_attempt_at": time.time() + 3600})

    def outbox(self):
        return [os.path.join(self.tmp.name, name)
                for name in os.listdir(self.tmp.name) if name.endswith(".eml")]

    def test_a_failure_reaches_the_configured_address(self):
        self.alert()
        messages = self.outbox()
        self.assertEqual(len(messages), 1)
        with open(messages[0], encoding="utf-8") as stream:
            body = stream.read()
        self.assertIn("To: alerts@example.com", body)
        self.assertIn("Automatic YouTube Music sign-in failed", body)
        self.assertIn("Google says the configured password is wrong.", body)
        self.assertIn("3 in a row", body)

    def test_the_alert_carries_no_web_link_and_no_code(self):
        self.alert()
        with open(self.outbox()[0], encoding="utf-8") as stream:
            body = stream.read().lower()
        # This is an alert, not a transactional message: nothing to click, nothing
        # to type. That keeps a forwarded alert inert.
        self.assertNotIn("verify-email", body)
        self.assertNotIn("one-time code", body)

    def test_without_a_recipient_nothing_is_mailed(self):
        os.environ.pop("LOGIN_FAIL_MAIL_ID", None)
        self.alert()
        self.assertEqual(self.outbox(), [])


if __name__ == "__main__":
    unittest.main()
