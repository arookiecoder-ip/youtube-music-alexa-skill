"""The player's own door: sign-up, verification, recovery, /account and self-delete.

A bare Flask app with the layer installed, a temporary database and a local mail
outbox -- ``server.py`` is not imported, because the blueprint must stand on its own
and because the middleware that decides which paths are public lives there and is
covered by its own tests.
"""
from __future__ import annotations

import email
import glob
import os
import re
import shutil
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from cryptography.fernet import Fernet
from flask import Flask

from multiuser import init_multiuser
from multiuser import netids
from multiuser.security import totp_at

TOKEN_RE = re.compile(r"/(verify-email|reset-password|magic|invite)/([A-Za-z0-9_-]+)")

PASSWORD = "Player!Passw0rd"
NEW_PASSWORD = "Player!Passw0rd-two"


class SelfServiceTestBase(unittest.TestCase):
    env_overrides: dict[str, str] = {}

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="selfservice-test-")
        self.outbox = os.path.join(self.tmp, "outbox")
        env = {
            "MULTI_USER_ENABLED": "1",
            "DB_FILE": os.path.join(self.tmp, "data.db"),
            "SECRET_KEY": "test-secret-key",
            "MULTIUSER_ADMIN_USER": "owner",
            "MULTIUSER_ADMIN_PASSWORD": "Owner!Passw0rd",
            "MULTIUSER_ADMIN_EMAIL": "owner@synthora.in",
            "MULTIUSER_ENCRYPTION_KEY": Fernet.generate_key().decode("ascii"),
            "MULTIUSER_MAIL_OUTBOX": self.outbox,
            # Blanked rather than merely unset: a machine that really deployed this
            # has live MULTIUSER_SMTP_* in its environment, and every suite run on it
            # would deliver the one-time links over the network and leave the outbox
            # empty, so every assertion below would fail for the wrong reason.
            "MULTIUSER_SMTP_HOST": "",
            "MULTIUSER_SMTP_USER": "",
            "MULTIUSER_MAIL_FROM": "",
            "MULTIUSER_PUBLIC_BASE_URL": "http://localhost",
            "MULTIUSER_REQUIRE_EMAIL_VERIFICATION": "0",
            "MULTIUSER_SIGNUP_MIN_SECONDS": "3",
        }
        env.update(self.env_overrides)
        self.env_patch = mock.patch.dict(os.environ, env, clear=False)
        self.env_patch.start()
        self.app = Flask(__name__)
        self.app.secret_key = "test-flask-session-secret"
        self.app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
        self.layer = init_multiuser(self.app)
        self.assertIsNotNone(self.layer)
        self.accounts = self.layer.accounts
        self.client = self.app.test_client()

    def tearDown(self):
        self.env_patch.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ----------------------------------------------------------------- helpers
    def form_csrf(self, path: str = "/signup") -> str:
        self.client.get(path)
        with self.client.session_transaction() as session:
            return session.get("mu_ss_csrf", "")

    def open_signup_form(self, age_seconds: float = 30.0) -> str:
        """Load the form and let it age, so the minimum-fill-time rule is met."""
        self.client.get("/signup")
        with self.client.session_transaction() as session:
            nonce = session.get("mu_ss_signup")
            self.assertTrue(nonce, "the form must issue its own nonce")
            session["mu_ss_signup"] = (nonce[0], time.time() - age_seconds)
            return session.get("mu_ss_csrf", "")

    def sign_in(self, user: dict) -> str:
        raw = self.accounts.create_session(user, ip="127.0.0.1", user_agent="pytest")
        with self.client.session_transaction() as session:
            session["mu_sid"] = raw
        self.client.get("/account")
        return raw

    def json_headers(self) -> dict:
        with self.client.session_transaction() as session:
            return {"X-CSRF-Token": session.get("mu_ss_csrf", "")}

    def register(
        self,
        username: str = "player1",
        email_address: str = "",
        *,
        age_seconds: float = 30.0,
        no_nonce: bool = False,
        csrf: str | None = None,
        **extra,
    ):
        """Post the sign-up form the way a browser would.

        ``age_seconds`` is how long the visitor is presumed to have spent typing
        (0 is faster than a person), ``no_nonce`` stands in for a form that was
        never opened, and ``csrf`` overrides the token outright.
        """
        token = self.open_signup_form(age_seconds)
        if no_nonce:
            with self.client.session_transaction() as session:
                session.pop("mu_ss_signup", None)
        payload = {
            "_csrf": token if csrf is None else csrf,
            "username": username,
            "email": email_address or f"{username}@example.test",
            "password": PASSWORD,
            "confirm_password": PASSWORD,
        }
        payload.update(extra)
        return self.client.post("/signup", data=payload)

    def newest_token(self, purpose: str) -> str:
        """The last link of this purpose ever written to the outbox.

        Searching every message rather than only the newest file: two mails in the
        same second are ordered by a random id, so "newest file" can be the wrong
        purpose entirely.
        """
        paths = sorted(glob.glob(os.path.join(self.outbox, "*.eml")))
        self.assertTrue(paths, "expected an outbox message")
        found: list[str] = []
        for path in paths:
            with open(path, "rb") as handle:
                message = email.message_from_bytes(handle.read())
            body = ""
            for part in message.walk():
                if part.get_content_type() == "text/plain":
                    body = (part.get_payload(decode=True) or b"").decode("utf-8", "replace")
            found.extend(
                match.group(2) for match in TOKEN_RE.finditer(body) if match.group(1) == purpose
            )
        self.assertTrue(found, f"no {purpose} link in {len(paths)} outbox messages")
        return found[-1]

    def exhaust(self, scope: str, key: str, limit: int, window: int) -> None:
        for _ in range(limit):
            self.layer.rate_limiter.allow(scope, key, limit, window)

    def csrf_now(self) -> str:
        """The session's current page token, without re-opening any form.

        Reading it off a GET would rotate state the test is trying to observe, so
        a consumed reset link came back as a CSRF failure instead of a 410.
        """
        with self.client.session_transaction() as session:
            return session.get("mu_ss_csrf", "")

    def user_row(self, username: str):
        """``get_user_by_username`` answers {} for a miss; tests read that as None."""
        return self.accounts.get_user_by_username(username) or None


class SignupTests(SelfServiceTestBase):
    def test_signup_creates_a_locked_account_and_posts_the_link(self):
        response = self.register("player1")
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True)[:400])
        user = self.user_row("player1")
        self.assertIsNotNone(user)
        self.assertEqual(user["role"], "user", "a self-signup can never grant itself a role")
        self.assertEqual(user["created_by"], "self")
        self.assertEqual(int(user["email_verified"]), 0)

        token = self.newest_token("verify-email")
        self.assertIsNone(
            self.accounts.authenticate("player1", PASSWORD, "127.0.0.1", "pytest")[0],
            "an unverified account must not sign in",
        )

        verified = self.client.get(f"/verify-email/{token}")
        self.assertEqual(verified.status_code, 200)
        signed_in, error = self.accounts.authenticate("player1", PASSWORD, "127.0.0.1", "pytest")
        self.assertIsNotNone(signed_in, error)
        self.assertEqual(int(self.user_row("player1")["email_verified"]), 1)

    def test_the_link_works_once(self):
        self.register("player1")
        token = self.newest_token("verify-email")
        self.client.get(f"/verify-email/{token}")
        second = self.client.get(f"/verify-email/{token}")
        self.assertEqual(second.status_code, 410)

    def test_self_signups_stay_locked_even_when_the_operator_disables_the_rule(self):
        # MULTIUSER_REQUIRE_EMAIL_VERIFICATION=0 is this suite's setting, and an
        # account created by an administrator is then usable at once. One created by
        # a stranger must still wait for the link, so the rule cannot be switched off
        # from the environment.
        admin = self.accounts.get_user_by_username("owner")
        self.assertNotEqual(admin.get("created_by"), "self")
        self.assertIsNotNone(self.accounts.authenticate(
            "owner", "Owner!Passw0rd", "127.0.0.1", "pytest")[0])

        self.register("player1")
        self.assertEqual(self.user_row("player1")["created_by"], "self")
        self.assertIsNone(self.accounts.authenticate(
            "player1", PASSWORD, "127.0.0.1", "pytest")[0])

    def test_honeypot_gets_a_fake_answer_and_no_account(self):
        response = self.register("bot123", website="http://spam.example")
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(self.user_row("bot123"))
        actions = [
            row["action"] for row in self.layer.db.fetchall(
                "SELECT action FROM audit_log ORDER BY id"
            )
        ]
        self.assertIn("signup_honeypot", actions)

    def test_a_form_submitted_faster_than_a_person_can_type_is_answered_fakely(self):
        response = self.register("quick123", age_seconds=0)
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(self.user_row("quick123"), "the too-fast rule must not create a user")

    def test_posting_without_opening_the_form_is_rejected_honestly(self):
        response = self.register("noform1", no_nonce=True)
        self.assertEqual(response.status_code, 400)
        self.assertIsNone(self.user_row("noform1"))

    def test_a_colliding_email_is_not_named_as_a_collision(self):
        self.register("player1")
        response = self.register("player2", email_address="player1@example.test")
        self.assertEqual(response.status_code, 409)
        body = response.get_data(as_text=True)
        # The typed address is echoed back (their own text, not a disclosure), but
        # nothing may say which of the two fields collided.
        self.assertNotIn("already in use", body)
        self.assertNotIn("player1", body.replace("player1@example.test", ""))

    def test_a_colliding_username_is_named_because_it_is_public(self):
        self.register("player1")
        response = self.register("player1")
        self.assertEqual(response.status_code, 409)
        self.assertIn("already in use", response.get_data(as_text=True))

    def test_a_stale_form_token_changes_nothing(self):
        response = self.client.post("/signup", data={
            "_csrf": "not-a-token", "username": "sneaky1", "email": "s@example.test",
            "password": PASSWORD, "confirm_password": PASSWORD,
        })
        self.assertEqual(response.status_code, 400)
        self.assertIsNone(self.user_row("sneaky1"))

    def test_signup_ip_budget_is_per_address_and_shared_across_attempts(self):
        token = self.open_signup_form()
        self.exhaust("signup-ip", netids.ip_bucket("127.0.0.1"), 5, 3600)
        response = self.client.post("/signup", data={
            "_csrf": token, "username": "over1", "email": "over1@example.test",
            "password": PASSWORD, "confirm_password": PASSWORD,
        })
        self.assertEqual(response.status_code, 429)
        self.assertIn("Retry-After", response.headers)
        self.assertIsNone(self.user_row("over1"))

    def test_weak_passwords_are_refused_per_field(self):
        token = self.open_signup_form()
        response = self.client.post("/signup", data={
            "_csrf": token, "username": "weak1", "email": "weak1@example.test",
            "password": "short", "confirm_password": "short",
        })
        self.assertEqual(response.status_code, 422)
        self.assertIn("at least", response.get_data(as_text=True))
        self.assertIsNone(self.user_row("weak1"))


class SignupDisabledTests(SelfServiceTestBase):
    env_overrides = {"MULTIUSER_MAX_SELF_SIGNUPS_PER_DAY": "0"}

    def test_a_daily_cap_of_zero_switches_the_door_shut_without_a_code_change(self):
        self.assertFalse(self.accounts.registration_open())
        response = self.client.get("/signup")
        self.assertEqual(response.status_code, 200, "the form still renders")
        token = self.open_signup_form()
        blocked = self.client.post("/signup", data={
            "_csrf": token, "username": "nobody1", "email": "nobody1@example.test",
            "password": PASSWORD, "confirm_password": PASSWORD,
        })
        self.assertEqual(blocked.status_code, 503)
        self.assertIsNone(self.user_row("nobody1"))


class SignupDisabledFlagTests(SelfServiceTestBase):
    env_overrides = {"MULTIUSER_SELF_SIGNUP_ENABLED": "0"}

    def test_the_signup_page_disappears_when_the_flag_is_off(self):
        response = self.client.get("/signup")
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("<input", response.get_data(as_text=True))


class ForgotPasswordTests(SelfServiceTestBase):
    def test_known_and_unknown_addresses_get_the_same_answer(self):
        created = self.register("player1")
        self.assertEqual(created.status_code, 200)
        before = len(glob.glob(os.path.join(self.outbox, "*.eml")))

        token = self.form_csrf("/forgot-password")
        known = self.client.post("/forgot-password", data={
            "_csrf": token, "identifier": "player1@example.test",
        })
        unknown = self.client.post("/forgot-password", data={
            "_csrf": token, "identifier": "ghost@example.test",
        })
        self.assertEqual(known.status_code, unknown.status_code)
        self.assertEqual(known.get_data(as_text=True), unknown.get_data(as_text=True))

        after = glob.glob(os.path.join(self.outbox, "*.eml"))
        self.assertEqual(len(after), before + 1, "only the real address gets a link")

    def test_the_reset_link_sets_a_password_and_kills_other_sessions(self):
        self.register("player1")
        self.client.get(f"/verify-email/{self.newest_token('verify-email')}")
        user = self.user_row("player1")
        other = self.accounts.create_session(user, ip="127.0.0.1", user_agent="other-device")

        token = self.form_csrf("/forgot-password")
        self.client.post("/forgot-password", data={"_csrf": token, "identifier": user["email"]})
        reset_token = self.newest_token("reset-password")
        page = self.client.get(f"/reset-password/{reset_token}")
        self.assertEqual(page.status_code, 200)
        form_token = self.form_csrf(f"/reset-password/{reset_token}")
        done = self.client.post(f"/reset-password/{reset_token}", data={
            "_csrf": form_token, "password": NEW_PASSWORD, "confirm_password": NEW_PASSWORD,
        })
        self.assertEqual(done.status_code, 200, done.get_data(as_text=True)[:400])

        signed_in, error = self.accounts.authenticate(
            "player1", NEW_PASSWORD, "127.0.0.1", "pytest")
        self.assertIsNotNone(signed_in, error)
        self.assertIsNone(self.accounts.validate_session(other)[0], "other devices must die")

    def test_a_reset_link_works_once(self):
        self.register("player1")
        token = self.form_csrf("/forgot-password")
        self.client.post("/forgot-password", data={
            "_csrf": token, "identifier": "player1@example.test"})
        reset_token = self.newest_token("reset-password")
        form_token = self.form_csrf(f"/reset-password/{reset_token}")
        self.client.post(f"/reset-password/{reset_token}", data={
            "_csrf": form_token, "password": NEW_PASSWORD, "confirm_password": NEW_PASSWORD})
        # A successful reset rotates the page token, so the replay must carry the
        # live one: otherwise it fails on CSRF and never reaches the used-link arm.
        replay = self.client.post(f"/reset-password/{reset_token}", data={
            "_csrf": self.csrf_now(), "password": NEW_PASSWORD, "confirm_password": NEW_PASSWORD})
        self.assertEqual(replay.status_code, 410)

    def test_the_forgot_budget_is_shared_with_the_administrator_door(self):
        # Same scope names and the same bucket function on both doors is the design;
        # this test is what stops either door from quietly getting its own budget.
        limit, window = 10, self.layer.config.login_window_seconds
        self.exhaust("forgot-ip", netids.ip_bucket("127.0.0.1"), limit, window)
        token = self.form_csrf("/forgot-password")
        response = self.client.post("/forgot-password", data={
            "_csrf": token, "identifier": "whoever@example.test"})
        self.assertEqual(response.status_code, 429)


class AccountPageTests(SelfServiceTestBase):
    def setUp(self):
        super().setUp()
        self.register("player1")
        self.client.get(f"/verify-email/{self.newest_token('verify-email')}")
        self.user = self.user_row("player1")

    def test_the_page_redirects_anonymously_instead_of_rendering_a_shell(self):
        response = self.client.get("/account")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response.headers["Location"])
        self.assertIn("next", response.headers["Location"])

    def test_the_json_endpoints_refuse_anonymous_browsers(self):
        for path in ("/api/account",):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 401, path)
        response = self.client.post(
            "/api/account/password", json={"current_password": PASSWORD, "password": NEW_PASSWORD,
                                           "confirm_password": NEW_PASSWORD})
        self.assertEqual(response.status_code, 401)

    def test_the_payload_carries_no_session_secret(self):
        self.sign_in(self.user)
        payload = self.client.get("/api/account").get_json()
        self.assertEqual(payload["username"], "player1")
        self.assertTrue(payload["email_verified"])
        self.assertFalse(payload["totp_enabled"])
        raw = str(payload)
        self.assertNotIn("sid_hash", raw)
        for row in payload["sessions"]:
            self.assertEqual(set(row), {"id", "current", "created_at", "last_seen_at",
                                       "expires_at", "ip", "device"})

    def test_json_writes_need_the_page_token(self):
        self.sign_in(self.user)
        response = self.client.post("/api/account/profile", json={"email": "x@example.test"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"]["code"], "csrf_failed")
        self.assertEqual(self.user_row("player1")["email"], self.user["email"])

    def test_a_revoked_or_expired_session_cannot_touch_the_account(self):
        raw = self.sign_in(self.user)
        session_row, _ = self.accounts.validate_session(raw)
        self.accounts.revoke_session(session_row["sid_hash"])
        self.assertEqual(self.client.get("/api/account").status_code, 401)

    def test_profile_change_resends_the_link_and_unverifies_the_address(self):
        self.sign_in(self.user)
        response = self.client.post("/api/account/profile",
                                    json={"email": "moved@example.test", "current_password": PASSWORD},
                                    headers=self.json_headers())
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True)[:300])
        body = response.get_json()
        self.assertTrue(body["email_changed"])
        self.assertTrue(body["verification_pending"])
        self.assertEqual(int(self.user_row("player1")["email_verified"]), 0)
        self.assertIn("not configured to send email", body["note"])

    def test_profile_change_without_the_password_is_a_403_on_one_field(self):
        self.sign_in(self.user)
        response = self.client.post("/api/account/profile",
                                    json={"email": "moved@example.test", "current_password": "nope"},
                                    headers=self.json_headers())
        self.assertEqual(response.status_code, 403)
        self.assertIn("current_password", response.get_json()["error"]["fields"])

    def test_password_change_keeps_this_browser_and_drops_the_others(self):
        other = self.accounts.create_session(self.user, ip="127.0.0.1", user_agent="stolen-laptop")
        raw = self.sign_in(self.user)
        response = self.client.post("/api/account/password", json={
            "current_password": PASSWORD, "password": NEW_PASSWORD,
            "confirm_password": NEW_PASSWORD}, headers=self.json_headers())
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True)[:300])
        self.assertIsNone(self.accounts.validate_session(other)[0],
                          "a stolen password must not keep a stranger's session alive")
        self.assertIsNotNone(self.accounts.validate_session(raw)[0])
        self.assertEqual(self.client.get("/api/account").status_code, 200)

    def test_password_change_refuses_a_weak_or_mismatched_choice(self):
        self.sign_in(self.user)
        response = self.client.post("/api/account/password", json={
            "current_password": PASSWORD, "password": "tiny", "confirm_password": "tiny"},
            headers=self.json_headers())
        self.assertEqual(response.status_code, 422)
        self.assertIn("password", response.get_json()["error"]["fields"])

    def test_signing_out_everywhere_kills_this_browser_too(self):
        self.accounts.create_session(self.user, ip="127.0.0.1", user_agent="other")
        self.sign_in(self.user)
        response = self.client.post("/api/account/sessions/revoke", json={"all": True},
                                    headers=self.json_headers())
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["signed_out"])
        self.assertEqual(self.client.get("/api/account").status_code, 401)

    def test_one_named_device_can_be_signed_out_alone(self):
        self.accounts.create_session(self.user, ip="127.0.0.1", user_agent="other-laptop")
        self.sign_in(self.user)
        payload = self.client.get("/api/account").get_json()
        other = [row for row in payload["sessions"] if not row["current"]][0]
        response = self.client.post("/api/account/sessions/revoke", json={"id": other["id"]},
                                    headers=self.json_headers())
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True)[:300])
        self.assertEqual(response.get_json()["revoked"], 1)
        self.assertEqual(self.client.get("/api/account").status_code, 200)

    def test_an_ambiguous_device_prefix_is_refused_instead_of_guessing(self):
        self.sign_in(self.user)
        response = self.client.post("/api/account/sessions/revoke", json={"id": "ff"},
                                    headers=self.json_headers())
        self.assertEqual(response.status_code, 422)

    def test_the_page_carries_headers_that_make_a_replay_useless(self):
        self.sign_in(self.user)
        headers = self.client.get("/account").headers
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(headers["X-Frame-Options"], "DENY")
        self.assertIn("script-src 'self'", headers["Content-Security-Policy"])
        self.assertNotIn("unsafe-inline", headers["Content-Security-Policy"])


class TwoFactorTests(SelfServiceTestBase):
    def setUp(self):
        super().setUp()
        self.register("player1")
        self.client.get(f"/verify-email/{self.newest_token('verify-email')}")
        self.user = self.user_row("player1")
        self.sign_in(self.user)

    def test_enrollment_needs_the_password_and_issues_a_secret_once(self):
        response = self.client.post("/api/account/totp/begin", json={"current_password": PASSWORD},
                                    headers=self.json_headers())
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True)[:300])
        secret = response.get_json()["secret"]
        self.assertTrue(secret)
        # The pending secret must not be reported as an enabled factor.
        payload = self.client.get("/api/account").get_json()
        self.assertFalse(payload["totp_enabled"])
        self.assertTrue(payload["totp_pending"])

        qr = self.client.get("/api/account/totp/qr")
        self.assertEqual(qr.status_code, 200)
        self.assertEqual(qr.mimetype, "image/svg+xml")
        self.assertIn(b"svg", qr.data[:400])

        code = totp_at(secret, int(time.time()) // 30)
        confirm = self.client.post("/api/account/totp/confirm", json={"code": code},
                                   headers=self.json_headers())
        self.assertEqual(confirm.status_code, 200, confirm.get_data(as_text=True)[:300])
        self.assertTrue(self.client.get("/api/account").get_json()["totp_enabled"])
        self.assertEqual(self.client.get("/api/account/totp/qr").status_code, 409)

    def test_a_wrong_code_leaves_the_factor_off(self):
        secret = self.client.post("/api/account/totp/begin", json={"current_password": PASSWORD},
                                  headers=self.json_headers()).get_json()["secret"]
        bad = self.client.post("/api/account/totp/confirm",
                               json={"code": "000000" if totp_at(secret, int(time.time()) // 30) != "000000" else "111111"},
                               headers=self.json_headers())
        self.assertEqual(bad.status_code, 403)
        self.assertFalse(self.client.get("/api/account").get_json()["totp_enabled"])

    def test_enrollment_can_be_cancelled_and_the_secret_is_then_gone(self):
        self.client.post("/api/account/totp/begin", json={"current_password": PASSWORD},
                         headers=self.json_headers())
        self.assertTrue(self.client.get("/api/account").get_json()["totp_pending"])
        self.client.post("/api/account/totp/cancel", json={}, headers=self.json_headers())
        payload = self.client.get("/api/account").get_json()
        self.assertFalse(payload["totp_pending"])
        self.assertEqual(self.client.get("/api/account/totp/qr").status_code, 409)

    def test_disabling_needs_both_the_password_and_a_live_code(self):
        secret = self.client.post("/api/account/totp/begin", json={"current_password": PASSWORD},
                                  headers=self.json_headers()).get_json()["secret"]
        self.client.post("/api/account/totp/confirm",
                         json={"code": totp_at(secret, int(time.time()) // 30)},
                         headers=self.json_headers())

        no_password = self.client.post("/api/account/totp/disable",
                                       json={"current_password": "wrong",
                                             "code": totp_at(secret, int(time.time()) // 30)},
                                       headers=self.json_headers())
        self.assertEqual(no_password.status_code, 403)
        self.assertTrue(self.client.get("/api/account").get_json()["totp_enabled"])

        no_code = self.client.post("/api/account/totp/disable",
                                   json={"current_password": PASSWORD, "code": "000000"},
                                   headers=self.json_headers())
        # One answer for a bad password and a bad code: the factor is off either
        # way and the caller learns nothing about which half it got wrong.
        self.assertEqual(no_code.status_code, 403)
        self.assertTrue(self.client.get("/api/account").get_json()["totp_enabled"])

        off = self.client.post("/api/account/totp/disable",
                               json={"current_password": PASSWORD,
                                     "code": totp_at(secret, int(time.time()) // 30)},
                               headers=self.json_headers())
        self.assertEqual(off.status_code, 200, off.get_data(as_text=True)[:300])
        self.assertFalse(self.client.get("/api/account").get_json()["totp_enabled"])

    def test_the_secret_never_appears_in_the_account_payload(self):
        secret = self.client.post("/api/account/totp/begin", json={"current_password": PASSWORD},
                                  headers=self.json_headers()).get_json()["secret"]
        raw = str(self.client.get("/api/account").get_json())
        self.assertNotIn(secret, raw)
        self.assertNotIn("totp_pending_enc", raw)


class DeleteAccountTests(SelfServiceTestBase):
    def setUp(self):
        super().setUp()
        self.purged = []
        self.accounts.set_purge_hook(lambda user_id: self.purged.append(user_id))
        self.register("player1")
        self.client.get(f"/verify-email/{self.newest_token('verify-email')}")
        self.user = self.user_row("player1")
        self.sign_in(self.user)

    def test_wrong_password_is_refused_and_changes_nothing(self):
        response = self.client.post("/api/account/delete",
                                    json={"password": "nope", "confirm_username": "player1"},
                                    headers=self.json_headers())
        self.assertEqual(response.status_code, 403)
        self.assertIsNotNone(self.user_row("player1"))
        self.assertEqual(self.purged, [])

    def test_typing_the_password_into_the_confirm_box_is_refused(self):
        response = self.client.post("/api/account/delete",
                                    json={"password": PASSWORD, "confirm_username": PASSWORD},
                                    headers=self.json_headers())
        self.assertEqual(response.status_code, 409)
        self.assertIsNotNone(self.user_row("player1"))

    def test_a_mismatched_username_is_refused(self):
        response = self.client.post("/api/account/delete",
                                    json={"password": PASSWORD, "confirm_username": "somebody"},
                                    headers=self.json_headers())
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()["error"]["code"], "username_mismatch")
        self.assertIsNotNone(self.user_row("player1"))

    def test_success_removes_the_rows_revokes_access_and_calls_the_purge_hook(self):
        other = self.accounts.create_session(self.user, ip="127.0.0.1", user_agent="phone")
        response = self.client.post("/api/account/delete",
                                    json={"password": PASSWORD, "confirm_username": "player1"},
                                    headers=self.json_headers())
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True)[:400])
        self.assertEqual(self.client.get("/api/account").status_code, 401)
        self.assertIsNone(self.user_row("player1"))
        self.assertIsNone(self.accounts.validate_session(other)[0])
        self.assertEqual(self.purged, [self.user["id"]])
        self.assertEqual(
            self.layer.db.fetchone("SELECT COUNT(*) AS n FROM user_sessions WHERE user_id = ?",
                                   (self.user["id"],))["n"], 0)

    def test_the_only_administrator_cannot_delete_themself(self):
        admin = self.accounts.get_user_by_username("owner")
        with self.client.session_transaction() as session:
            session["mu_sid"] = self.accounts.create_session(
                admin, ip="127.0.0.1", user_agent="pytest")
        self.client.get("/account")
        response = self.client.post("/api/account/delete",
                                    json={"password": "Owner!Passw0rd", "confirm_username": "owner"},
                                    headers=self.json_headers())
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()["error"]["code"], "last_owner")
        self.assertIsNotNone(self.accounts.get_user_by_username("owner"))

    def test_a_deleted_username_is_freed_for_the_next_person(self):
        self.client.post("/api/account/delete",
                         json={"password": PASSWORD, "confirm_username": "player1"},
                         headers=self.json_headers())
        response = self.register("player1")
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True)[:400])
        self.assertIsNotNone(self.user_row("player1"))


class LayerOffTests(unittest.TestCase):
    def test_the_door_does_not_exist_without_the_layer(self):
        app = Flask(__name__)
        app.secret_key = "x"
        with mock.patch.dict(os.environ, {"MULTI_USER_ENABLED": "0"}, clear=False):
            self.assertIsNone(init_multiuser(app))
        client = app.test_client()
        for path in ("/signup", "/forgot-password", "/account", "/api/account"):
            self.assertEqual(client.get(path).status_code, 404, path)


if __name__ == "__main__":
    unittest.main()
