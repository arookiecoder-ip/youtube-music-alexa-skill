import importlib.util
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from youtube_browser_session import (
    YouTubeBrowserSessionManager, accepted_browse_url, account_menu_is_signed_in,
    browser_client_is_signed_in, is_authentication_error,
    normalize_browser_headers, promote_browser_headers,
)


def signed_in_account_menu(name="Owner"):
    return {"actions": [{"openPopupAction": {"popup": {
        "multiPageMenuRenderer": {"header": {"activeAccountHeaderRenderer": {
            "accountName": {"runs": [{"text": name}]}
        }}}
    }}}]}


class FakeController:
    def __init__(self, states=None, candidate=None, delay=0):
        self.states = list(states or [{"state": "waiting_for_login"}])
        self.candidate = candidate or {
            "url": "https://music.youtube.com/youtubei/v1/browse?prettyPrint=false",
            "headers": {"cookie": "SAPISID=secret", "origin": "https://music.youtube.com"},
        }
        self.calls = []
        self.delay = delay

    def call(self, path, method="GET"):
        self.calls.append((path, method))
        if path == "/status":
            if self.delay: time.sleep(self.delay)
            return self.states.pop(0) if len(self.states) > 1 else self.states[0]
        if path == "/candidate/take": return self.candidate
        return {"state": "waiting_for_login"}


class LeaseTests(unittest.TestCase):
    def test_owner_binding_expiry_and_revocation(self):
        now = [100.0]
        manager = YouTubeBrowserSessionManager(FakeController(), lambda _: None,
                                               lease_ttl=10, clock=lambda: now[0])
        token, _ = manager.start("owner-a")
        self.assertTrue(manager.authorize("owner-a", token))
        self.assertFalse(manager.authorize("owner-b", token))  # Jam/other sid
        self.assertFalse(manager.authorize("owner-a", "wrong"))
        now[0] = 111
        self.assertFalse(manager.authorize("owner-a", token))
        token, _ = manager.start("owner-a")
        manager.revoke()
        self.assertFalse(manager.authorize("owner-a", token))

    def test_replacement_invalidates_previous_lease(self):
        manager = YouTubeBrowserSessionManager(FakeController(), lambda _: None)
        first, _ = manager.start("owner")
        second, _ = manager.start("owner")
        self.assertFalse(manager.authorize("owner", first))
        self.assertTrue(manager.authorize("owner", second))

    def test_explicit_browser_controls_are_proxied_to_sidecar(self):
        controller = FakeController()
        manager = YouTubeBrowserSessionManager(controller, lambda _: None)
        manager.open_youtube()
        manager.request_capture()
        self.assertIn(("/interactive/open-youtube", "POST"), controller.calls)
        self.assertIn(("/interactive/capture", "POST"), controller.calls)


class FlaskAuthorizationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import server
        cls.server = server
        cls.client = server.app.test_client()

    def test_start_rejects_unauthenticated_and_jam_callers(self):
        with mock.patch.object(self.server, "_logged_in", return_value=False):
            response = self.client.post("/api/youtube/browser-session/start", json={})
        self.assertIn(response.status_code, (302, 401))

    def test_owner_start_sets_scoped_httponly_lease_cookie(self):
        status = {"state": "waiting_for_login", "available": True,
                  "refreshing": False, "reconnect_required": False,
                  "last_successful_renewal": None, "message": None,
                  "lease_expires_at": time.time() + 300}
        with mock.patch.object(self.server, "_logged_in", return_value=True), \
             mock.patch.object(self.server, "_browser_owner_id", return_value="owner"), \
             mock.patch.object(self.server._youtube_browser_sessions, "start",
                               return_value=("lease-token", status)):
            response = self.client.post("/api/youtube/browser-session/start", json={})
        cookie = response.headers.get("Set-Cookie", "")
        self.assertEqual(response.status_code, 200)
        self.assertIn("ui=2", response.get_json()["url"])
        self.assertIn("HttpOnly", cookie)
        self.assertIn("Path=/youtube-login/", cookie)
        self.assertIn("SameSite=Lax", cookie)

    def test_explicit_browser_controls_require_owner_session(self):
        with mock.patch.object(self.server, "_logged_in", return_value=False):
            for path in ("/api/youtube/browser-session/open-youtube",
                         "/api/youtube/browser-session/capture"):
                response = self.client.post(path, json={})
                self.assertIn(response.status_code, (302, 401))

    def test_owner_capture_proxies_without_exposing_control_token(self):
        status = {"state": "capture_requested", "available": True,
                  "refreshing": False, "reconnect_required": False,
                  "last_successful_renewal": None, "message": None,
                  "lease_expires_at": time.time() + 300}
        with mock.patch.object(self.server, "_logged_in", return_value=True), \
             mock.patch.object(self.server._youtube_browser_sessions, "request_capture",
                               return_value=status) as capture:
            response = self.client.post("/api/youtube/browser-session/capture", json={})
        self.assertEqual(response.status_code, 202)
        capture.assert_called_once_with()
        self.assertNotIn("control", response.get_data(as_text=True).lower())


    def test_youtube_status_requires_a_working_signed_in_client(self):
        class BrowserClient:
            auth_type = "BROWSER"
            def _send_request(self, endpoint, body):
                self.endpoint, self.body = endpoint, body
                return signed_in_account_menu()

        class AnonymousClient:
            auth_type = "UNAUTHORIZED"

        class ExpiredClient(BrowserClient):
            def _send_request(self, endpoint, body):
                raise RuntimeError("login required")

        self.assertTrue(self.server._ytmusic_client_is_authenticated(BrowserClient()))
        self.assertFalse(self.server._ytmusic_client_is_authenticated(AnonymousClient()))
        self.assertFalse(self.server._ytmusic_client_is_authenticated(ExpiredClient()))

    def test_amazon_proxy_uses_caddy_for_compose_localhost(self):
        with self.server.app.test_request_context(
                "/alexa/proxy_login/", method="POST",
                json={"email": "owner@example.com", "password": "secret"}), \
                mock.patch.dict(os.environ, {"ALEXA_PROXY_BEHIND_CADDY": "1"}), \
                mock.patch.object(self.server.alexa_remote.remote, "is_logged_in",
                                  return_value=False), \
                mock.patch.object(self.server.alexa_remote.remote, "proxy_start_url",
                                  return_value=("http://localhost/alexa/proxy/", None)) as start:
            response = self.server.alexa_proxy_login()

        self.assertEqual(response.status_code, 200)
        start.assert_called_once_with("owner@example.com", "secret", base_url=None)

    def test_amazon_proxy_keeps_direct_local_development_fallback(self):
        with self.server.app.test_request_context(
                "/alexa/proxy_login/", method="POST",
                json={"email": "owner@example.com", "password": "secret"}), \
                mock.patch.dict(os.environ, {"ALEXA_PROXY_BEHIND_CADDY": "0"}), \
                mock.patch.object(self.server.alexa_remote.remote, "is_logged_in",
                                  return_value=False), \
                mock.patch.object(self.server.alexa_remote.remote, "proxy_start_url",
                                  return_value=("http://127.0.0.1:5001/alexa/proxy/", None)) as start:
            response = self.server.alexa_proxy_login()

        self.assertEqual(response.status_code, 200)
        start.assert_called_once_with(
            "owner@example.com", "secret",
            base_url="http://127.0.0.1:5001/alexa/proxy/")


class CaptureTests(unittest.TestCase):
    def test_origin_and_path_filter(self):
        self.assertTrue(accepted_browse_url("https://music.youtube.com/youtubei/v1/browse?x=1"))
        self.assertTrue(accepted_browse_url("https://music.youtube.com/youtubei/v1/player"))
        for url in ("http://music.youtube.com/youtubei/v1/browse",
                    "https://youtube.com/youtubei/v1/browse",
                    "https://music.youtube.com/youtubei/v2/player",
                    "https://music.youtube.com.evil.test/youtubei/v1/browse"):
            self.assertFalse(accepted_browse_url(url))

    def test_normalization_requires_cookie(self):
        with self.assertRaises(ValueError):
            normalize_browser_headers({"origin": "https://music.youtube.com"})
        raw = normalize_browser_headers("curl 'https://music.youtube.com' -H 'origin: https://music.youtube.com' -b 'SAPISID=x'")
        self.assertIn("cookie: SAPISID=x", raw)
        self.assertIn("x-goog-authuser: 0", raw)

    def test_consumed_candidate_does_not_reset_validation_error(self):
        controller = FakeController(states=[{"state": "candidate_taken"}])
        manager = YouTubeBrowserSessionManager(controller, lambda _: None)
        manager._state = "reconnect_required"
        manager._message = "Captured session was not accepted"
        status = manager.poll()
        self.assertEqual(status["state"], "reconnect_required")
        self.assertNotIn(("/candidate/take", "POST"), controller.calls)

    def test_controller_rejects_missing_or_wrong_token(self):
        path = Path(__file__).parents[1] / "browser-auth" / "service.py"
        spec = importlib.util.spec_from_file_location("browser_service_test", path)
        module = importlib.util.module_from_spec(spec)
        with mock.patch.dict(os.environ, {"YT_BROWSER_CONTROL_TOKEN": "correct"}):
            spec.loader.exec_module(module)
        client = module.app.test_client()
        self.assertEqual(client.get("/status").status_code, 401)
        self.assertEqual(client.get("/status", headers={"X-Control-Token": "wrong"}).status_code, 401)
        self.assertEqual(client.get("/status", headers={"X-Control-Token": "correct"}).status_code, 200)

    def test_sidecar_rejects_signed_headers_without_active_account(self):
        path = Path(__file__).parents[1] / "browser-auth" / "service.py"
        spec = importlib.util.spec_from_file_location("browser_service_account_test", path)
        module = importlib.util.module_from_spec(spec)
        with mock.patch.dict(os.environ, {"YT_BROWSER_CONTROL_TOKEN": "correct"}):
            spec.loader.exec_module(module)
        self.assertTrue(module.account_menu_is_signed_in(signed_in_account_menu()))
        self.assertFalse(module.account_menu_is_signed_in({"actions": [{
            "openPopupAction": {"popup": {"multiPageMenuRenderer": {
                "sections": []
            }}}
        }]}))

    def test_sidecar_identifies_signed_out_account_menu(self):
        path = Path(__file__).parents[1] / "browser-auth" / "service.py"
        spec = importlib.util.spec_from_file_location("browser_service_signed_out_test", path)
        module = importlib.util.module_from_spec(spec)
        with mock.patch.dict(os.environ, {"YT_BROWSER_CONTROL_TOKEN": "correct"}):
            spec.loader.exec_module(module)
        signed_out = {"actions": [{"openPopupAction": {"popup": {
            "multiPageMenuRenderer": {"sections": []}
        }}}]}
        self.assertTrue(module.account_menu_reports_signed_out(signed_out))
        self.assertFalse(module.account_menu_reports_signed_out(signed_in_account_menu()))
        self.assertFalse(module.account_menu_reports_signed_out({}))

    def test_google_login_process_has_fixed_cdp_but_no_automation_flag(self):
        path = Path(__file__).parents[1] / "browser-auth" / "service.py"
        spec = importlib.util.spec_from_file_location("browser_service_launch_test", path)
        module = importlib.util.module_from_spec(spec)
        with mock.patch.dict(os.environ, {"YT_BROWSER_CONTROL_TOKEN": "correct"}):
            spec.loader.exec_module(module)
        login_command = module._plain_chromium_command(enable_capture=True)
        self.assertNotIn("--enable-automation", login_command)
        self.assertNotIn("--remote-debugging-pipe", login_command)
        self.assertIn("--remote-debugging-port=9222", login_command)
        self.assertIn("--remote-debugging-address=127.0.0.1", login_command)
        self.assertIn("--hide-crash-restore-bubble", login_command)

        capture_command = module._plain_chromium_command(enable_capture=True)
        self.assertNotIn("--enable-automation", capture_command)
        self.assertNotIn("--remote-debugging-pipe", capture_command)
        self.assertIn("--remote-debugging-port=9222", capture_command)

    def test_failed_capture_keeps_same_chromium_process_for_retry(self):
        path = Path(__file__).parents[1] / "browser-auth" / "service.py"
        spec = importlib.util.spec_from_file_location("browser_service_same_process", path)
        module = importlib.util.module_from_spec(spec)
        with mock.patch.dict(os.environ, {"YT_BROWSER_CONTROL_TOKEN": "correct"}):
            spec.loader.exec_module(module)
        with mock.patch.object(module, "_manual_login") as launch, \
             mock.patch.object(module, "_capture_from_running_browser",
                               return_value="signed_out") as validate, \
             mock.patch.object(module, "_stop_manual_browser") as stop:
            worker = threading.Thread(target=module._capture, args=(True,))
            worker.start()
            deadline = time.time() + 2
            while module._public_state()["state"] != "waiting_for_login" and time.time() < deadline:
                time.sleep(0.01)
            module._capture_requested.set()
            deadline = time.time() + 2
            while validate.call_count == 0 and time.time() < deadline:
                time.sleep(0.01)
            self.assertEqual(module._public_state()["state"], "waiting_for_login")
            self.assertTrue(module._public_state()["interactive"])
            module._set_state("idle", interactive=False)
            worker.join(timeout=2)
        launch.assert_called_once_with(enable_capture=True)
        validate.assert_called_once_with(True)
        stop.assert_called_once_with()

    def test_capture_is_explicit_and_requires_running_plain_browser(self):
        path = Path(__file__).parents[1] / "browser-auth" / "service.py"
        spec = importlib.util.spec_from_file_location("browser_service_capture", path)
        module = importlib.util.module_from_spec(spec)
        with mock.patch.dict(os.environ, {"YT_BROWSER_CONTROL_TOKEN": "correct"}):
            spec.loader.exec_module(module)
        client = module.app.test_client()
        headers = {"X-Control-Token": "correct"}
        self.assertEqual(client.post("/interactive/capture", headers=headers).status_code, 409)
        module._set_state("waiting_for_login", interactive=True)
        response = client.post("/interactive/capture", headers=headers)
        self.assertEqual(response.status_code, 202)
        self.assertTrue(module._capture_requested.is_set())

    def test_novnc_controls_use_owner_api_not_sidecar_token(self):
        script = (Path(__file__).parents[1] / "browser-auth" / "novnc-controls.js").read_text()
        self.assertIn("/api/youtube/browser-session/", script)
        self.assertIn('"Content-Type": "application/json"', script)
        self.assertIn('body: "{}"', script)
        self.assertIn("open-youtube", script)
        self.assertIn("capture", script)
        self.assertNotIn("YT_BROWSER_CONTROL_TOKEN", script)

class PromotionTests(unittest.TestCase):
    def test_account_menu_requires_real_active_account(self):
        self.assertTrue(account_menu_is_signed_in(signed_in_account_menu()))
        signed_out = {"actions": [{"openPopupAction": {"popup": {
            "multiPageMenuRenderer": {"sections": []}
        }}}]}
        self.assertFalse(account_menu_is_signed_in(signed_out))
        self.assertFalse(account_menu_is_signed_in({}))

    def test_browser_client_uses_account_menu_endpoint(self):
        client = mock.Mock()
        client._send_request.return_value = signed_in_account_menu()
        self.assertTrue(browser_client_is_signed_in(client))
        client._send_request.assert_called_once_with("account/account_menu", {})

    def test_atomic_promotion_after_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            dest = os.path.join(directory, "auth.json")
            Path(dest).write_text("old", encoding="utf-8")
            validated = []
            def setup(filepath, headers_raw): Path(filepath).write_text("new", encoding="utf-8")
            class YT:
                def _send_request(self, endpoint, body):
                    validated.append(True)
                    return signed_in_account_menu()
            promote_browser_headers({"cookie": "SAPISID=x"}, dest, setup, lambda _: YT())
            self.assertEqual(Path(dest).read_text(encoding="utf-8"), "new")
            self.assertEqual(validated, [True])

    def test_invalid_capture_preserves_old_file(self):
        with tempfile.TemporaryDirectory() as directory:
            dest = os.path.join(directory, "auth.json")
            Path(dest).write_text("old", encoding="utf-8")
            def setup(filepath, headers_raw): Path(filepath).write_text("bad", encoding="utf-8")
            class YT:
                def _send_request(self, endpoint, body):
                    return {"actions": []}
            with self.assertRaisesRegex(ValueError, "still signed out"):
                promote_browser_headers({"cookie": "bad"}, dest, setup, lambda _: YT())
            self.assertEqual(Path(dest).read_text(encoding="utf-8"), "old")


class RenewalTests(unittest.TestCase):
    def test_capture_promotes_and_invalidates_cache(self):
        promoted, invalidated = [], []
        ctl = FakeController(states=[{"state": "captured"}])
        manager = YouTubeBrowserSessionManager(ctl, promoted.append,
                                               lambda: invalidated.append(True), refresh_cooldown=0)
        self.assertTrue(manager.refresh())
        self.assertEqual(len(promoted), 1)
        self.assertEqual(invalidated, [True])
        self.assertEqual(manager.status()["state"], "connected")
        self.assertIn(("/interactive/complete", "POST"), ctl.calls)

    def test_single_flight_concurrent_refresh(self):
        ctl = FakeController(states=[{"state": "captured"}], delay=.05)
        manager = YouTubeBrowserSessionManager(ctl, lambda _: None,
                                               refresh_cooldown=0, refresh_timeout=2)
        results = []
        threads = [threading.Thread(target=lambda: results.append(manager.refresh())) for _ in range(5)]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        self.assertEqual(sum(1 for path, _ in ctl.calls if path == "/refresh"), 1)
        self.assertTrue(all(results))

    def test_one_retry_only(self):
        manager = YouTubeBrowserSessionManager(FakeController(states=[{"state": "captured"}]),
                                               lambda _: None, refresh_cooldown=0)
        calls = []
        def operation():
            calls.append(1)
            raise RuntimeError("login required")
        with self.assertRaises(RuntimeError): manager.call_with_one_refresh(operation)
        self.assertEqual(len(calls), 2)

    def test_non_auth_error_does_not_refresh(self):
        ctl = FakeController()
        manager = YouTubeBrowserSessionManager(ctl, lambda _: None)
        with self.assertRaises(RuntimeError):
            manager.call_with_one_refresh(lambda: (_ for _ in ()).throw(RuntimeError("JSON parse changed")))
        self.assertFalse(any(path == "/refresh" for path, _ in ctl.calls))
        self.assertFalse(is_authentication_error(RuntimeError("rate limit")))

    def test_signed_out_profile_requires_reconnect(self):
        ctl = FakeController(states=[{"state": "reconnect_required", "message": "login"}])
        manager = YouTubeBrowserSessionManager(ctl, lambda _: None,
                                               refresh_cooldown=0, refresh_timeout=.2)
        self.assertFalse(manager.refresh())
        self.assertTrue(manager.status()["reconnect_required"])


if __name__ == "__main__":
    unittest.main()
