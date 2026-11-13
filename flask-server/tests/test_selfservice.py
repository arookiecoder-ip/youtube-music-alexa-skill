"""The player's own door: sign-up, verification, recovery, /account and self-delete.

A bare Flask app with the layer installed, a temporary database and a local mail
outbox -- ``server.py`` is not imported, because the blueprint must stand on its own
and because the middleware that decides which paths are public lives there and is
covered by its own tests.
"""
from __future__ import annotations

import email
from email.utils import parseaddr
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

TOKEN_RE = re.compile(r"/(magic|invite)/([A-Za-z0-9_-]+)")
CODE_RE = re.compile(r"Your code is (\d{6})")
# What each code email says at the top, which is the only thing that tells a
# reader of the outbox whether a six-digit code is for verification or a reset.
CODE_HEADINGS = {
    "verify": "Verify your email address",
    "reset": "Reset your Music Box password",
    "reauth": "Confirm a security change",
}

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
        self.layer.db.execute("UPDATE users SET must_change_password=0 WHERE created_by='system'")
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
        raw = self.accounts.create_session(self.accounts.get_user(user["id"]), ip="127.0.0.1", user_agent="pytest")
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
        self.pending_signup_email = email_address or f"{username}@example.test"
        self.pending_signup_details = {
            "username": username,
            "password": extra.pop("password", PASSWORD),
            "confirm_password": extra.pop("confirm_password", PASSWORD),
        }
        payload = {
            "_csrf": token if csrf is None else csrf,
            "step": "email",
            "email": email_address or f"{username}@example.test",
        }
        payload.update(extra)
        self.pending_signup_outbox = set(glob.glob(os.path.join(self.outbox, "*.eml")))
        return self.client.post("/signup", data=payload)

    def _outbox_bodies(self) -> list[str]:
        """The text part of every message written so far, oldest first.

        Searching every message rather than only the newest file: two mails in the
        same second are ordered by a random id, so "newest file" can be the wrong
        purpose entirely.
        """
        paths = sorted(glob.glob(os.path.join(self.outbox, "*.eml")))
        self.assertTrue(paths, "expected an outbox message")
        bodies: list[str] = []
        for path in paths:
            with open(path, "rb") as handle:
                message = email.message_from_bytes(handle.read())
            body = ""
            for part in message.walk():
                if part.get_content_type() == "text/plain":
                    body = (part.get_payload(decode=True) or b"").decode("utf-8", "replace")
            bodies.append(body)
        return bodies

    def newest_token(self, purpose: str) -> str:
        """The last *link* of this purpose in the outbox: invitation or magic only.

        Verification and reset are typed codes now, so asking this for them is a
        test that has not been updated, not a product bug.
        """
        found: list[str] = []
        for body in self._outbox_bodies():
            found.extend(
                match.group(2) for match in TOKEN_RE.finditer(body) if match.group(1) == purpose
            )
        self.assertTrue(found, f"no {purpose} link in the outbox")
        return found[-1]

    def newest_code(self, purpose: str) -> str:
        """The six digits from the newest code email of this purpose."""
        heading = CODE_HEADINGS[purpose]
        found: list[str] = []
        bodies = self._outbox_bodies()
        if purpose == "verify" and getattr(self, "pending_signup_details", None):
            # Two signup messages can share a timestamp; select the current
            # recipient instead of relying on the filename's random suffix.
            bodies = []
            for path in sorted(glob.glob(os.path.join(self.outbox, "*.eml"))):
                with open(path, "rb") as handle:
                    message = email.message_from_bytes(handle.read())
                if path in self.pending_signup_outbox or parseaddr(message.get("To", ""))[1] != self.pending_signup_email:
                    continue
                for part in message.walk():
                    if part.get_content_type() == "text/plain":
                        bodies.append((part.get_payload(decode=True) or b"").decode("utf-8", "replace"))
        for body in bodies:
            if heading in body:
                found.extend(match.group(1) for match in CODE_RE.finditer(body))
        self.assertTrue(found, f"no {purpose} code in the outbox")
        return found[-1]

    def verify_by_code(self, username: str = "player1", code: str = ""):
        """Complete the current staged signup, or verify an existing account."""
        details = getattr(self, "pending_signup_details", None)
        if details is not None:
            verified = self.client.post("/signup", data={
                "_csrf": self.form_csrf("/signup"), "step": "code",
                "code": code or self.newest_code("verify"),
            })
            if verified.status_code != 303:
                return verified
            response = self.client.post("/signup", data={
                "_csrf": self.form_csrf("/signup"), "step": "details", **details,
            })
            if response.status_code == 200:
                self.pending_signup_details = None
            return response
        return self.client.post("/verify", data={
            "_csrf": self.form_csrf("/verify"),
            "username": username,
            "code": code or self.newest_code("verify"),
        })

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
    def test_signup_proves_email_before_creating_an_account(self):
        response = self.register("player1")
        self.assertEqual(response.status_code, 303)
        self.assertIsNone(self.user_row("player1"))
        self.assertIsNone(self.accounts.authenticate("player1", PASSWORD, "127.0.0.1", "pytest")[0])
        self.assertEqual(self.verify_by_code().status_code, 200)
        user = self.user_row("player1")
        self.assertEqual(user["role"], "user")
        self.assertEqual(user["created_by"], "self")
        self.assertEqual(int(user["email_verified"]), 1)
        self.assertIsNotNone(self.accounts.authenticate("player1", PASSWORD, "127.0.0.1", "pytest")[0])

    def test_a_signup_code_cannot_be_replayed_after_account_creation(self):
        self.register("player1")
        code = self.newest_code("verify")
        self.assertEqual(self.verify_by_code(code=code).status_code, 200)
        replay = self.client.post("/signup", data={
            "_csrf": self.form_csrf("/signup"), "step": "code", "code": code,
        })
        self.assertEqual(replay.status_code, 400)
        self.assertEqual(int(self.user_row("player1")["email_verified"]), 1)

    def test_email_proof_is_required_even_when_operator_disables_legacy_rule(self):
        self.assertIsNotNone(self.accounts.authenticate("owner", "Owner!Passw0rd", "127.0.0.1", "pytest")[0])
        self.register("player1")
        self.assertIsNone(self.user_row("player1"))
        self.assertIsNone(self.accounts.authenticate("player1", PASSWORD, "127.0.0.1", "pytest")[0])

    def test_honeypot_is_rejected_without_sending_mail_or_creating_account(self):
        response = self.register("bot123", website="http://spam.example")
        self.assertEqual(response.status_code, 400)
        self.assertIsNone(self.user_row("bot123"))
        self.assertEqual(glob.glob(os.path.join(self.outbox, "*.eml")), [])

    def test_a_form_submitted_too_fast_creates_no_challenge_or_account(self):
        response = self.register("quick123", age_seconds=0)
        self.assertEqual(response.status_code, 400)
        self.assertIsNone(self.user_row("quick123"))
        self.assertEqual(glob.glob(os.path.join(self.outbox, "*.eml")), [])

    def test_posting_without_opening_the_form_is_rejected(self):
        self.assertEqual(self.register("noform1", no_nonce=True).status_code, 400)
        self.assertIsNone(self.user_row("noform1"))

    def test_an_existing_email_is_refused_before_details(self):
        self.register("player1")
        self.assertEqual(self.verify_by_code().status_code, 200)
        response = self.register("player2", email_address="player1@example.test")
        self.assertEqual(response.status_code, 409)
        self.assertIsNone(self.user_row("player2"))
        self.assertNotIn("player1", response.text.replace("player1@example.test", ""))

    def test_a_public_username_collision_is_refused_at_details(self):
        self.register("player1")
        self.assertEqual(self.verify_by_code().status_code, 200)
        self.assertEqual(self.register("player1", email_address="another@example.test").status_code, 303)
        response = self.verify_by_code()
        self.assertEqual(response.status_code, 422)
        self.assertIn("already in use", response.text)

    def test_a_stale_form_token_changes_nothing(self):
        response = self.client.post("/signup", data={
            "_csrf": "not-a-token", "step": "email", "email": "s@example.test",
        })
        self.assertEqual(response.status_code, 400)
        self.assertIsNone(self.user_row("sneaky1"))

    def test_signup_ip_budget_is_per_address_and_shared_across_attempts(self):
        token = self.open_signup_form()
        self.exhaust("signup-ip", netids.ip_bucket("127.0.0.1"), 5, 3600)
        response = self.client.post("/signup", data={
            "_csrf": token, "step": "email", "email": "over1@example.test",
        })
        self.assertEqual(response.status_code, 429)
        self.assertIn("Retry-After", response.headers)
        self.assertIsNone(self.user_row("over1"))

    def test_weak_passwords_are_refused_after_email_proof(self):
        self.assertEqual(self.register("weak1", password="short", confirm_password="short").status_code, 303)
        response = self.verify_by_code()
        self.assertEqual(response.status_code, 422)
        self.assertIn("at least", response.text)
        self.assertIsNone(self.user_row("weak1"))


class SignupDisabledTests(SelfServiceTestBase):
    env_overrides = {"MULTIUSER_MAX_SELF_SIGNUPS_PER_DAY": "0"}

    def test_a_daily_cap_of_zero_switches_the_door_shut_without_a_code_change(self):
        self.assertFalse(self.accounts.registration_open())
        response = self.client.get("/signup")
        # A form that cannot be used is not a courtesy: the closed page says when
        # to come back instead of letting the visitor type into a dead door.
        self.assertEqual(response.status_code, 503, "the form is not offered at all")
        self.assertNotIn("<input", response.get_data(as_text=True))
        blocked = self.client.post("/signup", data={
            "_csrf": self.form_csrf("/signup"), "username": "nobody1",
            "email": "nobody1@example.test",
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
        self.assertEqual(self.register("player1").status_code, 303)
        self.assertEqual(self.verify_by_code().status_code, 200)
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

    def test_the_reset_code_sets_a_password_and_kills_other_sessions(self):
        self.register("player1")
        self.verify_by_code()
        user = self.user_row("player1")
        other = self.accounts.create_session(user, ip="127.0.0.1", user_agent="other-device")

        token = self.form_csrf("/forgot-password")
        self.client.post("/forgot-password", data={"_csrf": token, "identifier": user["email"]})
        page = self.client.get("/reset-password")
        self.assertEqual(page.status_code, 200)
        done = self.client.post("/reset-password", data={
            "_csrf": self.form_csrf("/reset-password"),
            "identifier": user["email"],
            "code": self.newest_code("reset"),
            "password": NEW_PASSWORD, "confirm_password": NEW_PASSWORD,
        })
        self.assertEqual(done.status_code, 200, done.get_data(as_text=True)[:400])

        signed_in, error = self.accounts.authenticate(
            "player1", NEW_PASSWORD, "127.0.0.1", "pytest")
        self.assertIsNotNone(signed_in, error)
        self.assertIsNone(self.accounts.validate_session(other)[0], "other devices must die")

    def test_a_reset_code_works_once(self):
        self.register("player1")
        self.assertEqual(self.verify_by_code().status_code, 200)
        token = self.form_csrf("/forgot-password")
        self.client.post("/forgot-password", data={
            "_csrf": token, "identifier": "player1@example.test"})
        code = self.newest_code("reset")
        form_token = self.form_csrf("/reset-password")
        self.client.post("/reset-password", data={
            "_csrf": form_token, "identifier": "player1", "code": code,
            "password": NEW_PASSWORD, "confirm_password": NEW_PASSWORD})
        # A successful reset rotates the page token, so the replay must carry the
        # live one: otherwise it fails on CSRF and never reaches the used-code arm.
        replay = self.client.post("/reset-password", data={
            "_csrf": self.csrf_now(), "identifier": "player1", "code": code,
            "password": NEW_PASSWORD, "confirm_password": NEW_PASSWORD})
        self.assertEqual(replay.status_code, 410, replay.get_data(as_text=True)[:400])

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
        self.verify_by_code()
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
                                       "expires_at", "ip", "device", "device_ua",
                                       "seen_text", "signed_in_text"})
        # The row a person scans is a name, not a header: a 120-character
        # "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like" is
        # evidence, and it belongs one line down where it does not hide the device.
        labels = [row["device"] for row in payload["sessions"]]
        self.assertTrue(all("Mozilla/5.0" not in label for label in labels), labels)

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
        code=self.accounts.create_email_code(self.user['id'],'reauth')
        response=self.client.post('/api/account/profile',json={'email':'moved@example.test','current_password':PASSWORD,'email_code':code},headers=self.json_headers())
        self.assertEqual(response.status_code,200,response.get_data(as_text=True)[:300])
        self.assertTrue(response.json['verification_pending'])
        self.assertEqual(self.user_row('player1')['email'],self.user['email'])
        self.assertEqual(self.user_row('player1')['pending_email'],'moved@example.test')
        self.assertIn('not configured to send email',response.json['note'])

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

    def test_the_popup_reset_takes_a_mailed_code_and_keeps_this_browser(self):
        other = self.accounts.create_session(self.user, ip="127.0.0.1", user_agent="other")
        self.sign_in(self.user)
        sent = self.client.post("/api/account/password/reset/send", json={},
                                headers=self.json_headers())
        self.assertEqual(sent.status_code, 200, sent.get_data(as_text=True)[:300])
        self.assertIn("@", sent.get_json()["sent_to"], "the answer masks the address")
        code = self.newest_code("reset")

        wrong = self.client.post("/api/account/password/reset", json={
            "code": "000000", "password": NEW_PASSWORD,
            "confirm_password": NEW_PASSWORD, "sign_out_others": False},
            headers=self.json_headers())
        self.assertEqual(wrong.status_code, 410)
        self.assertIsNotNone(self.accounts.authenticate(
            "player1", PASSWORD, "127.0.0.1", "pytest")[0], "a bad code changes nothing")

        done = self.client.post("/api/account/password/reset", json={
            "code": code, "password": NEW_PASSWORD, "confirm_password": NEW_PASSWORD,
            "sign_out_others": False}, headers=self.json_headers())
        self.assertEqual(done.status_code, 200, done.get_data(as_text=True)[:300])
        self.assertIsNotNone(self.accounts.authenticate(
            "player1", NEW_PASSWORD, "127.0.0.1", "pytest")[0])
        # Unticked: the person asked to keep their other devices signed in, and the
        # reset still proves the mailbox, which is all a reset ever asked for.
        self.assertIsNotNone(self.accounts.validate_session(other)[0])
        self.assertEqual(self.client.get("/api/account").status_code, 200)

    def test_the_popup_reset_signs_out_other_devices_unless_told_not_to(self):
        other = self.accounts.create_session(self.user, ip="127.0.0.1", user_agent="other")
        raw = self.sign_in(self.user)
        self.client.post("/api/account/password/reset/send", json={}, headers=self.json_headers())
        done = self.client.post("/api/account/password/reset", json={
            "code": self.newest_code("reset"), "password": NEW_PASSWORD,
            "confirm_password": NEW_PASSWORD}, headers=self.json_headers())
        self.assertEqual(done.status_code, 200, done.get_data(as_text=True)[:300])
        self.assertIn("Every other device", done.get_json()["message"])
        self.assertIsNone(self.accounts.validate_session(other)[0])
        self.assertIsNotNone(self.accounts.validate_session(raw)[0])
        self.assertEqual(self.client.get("/api/account").status_code, 200)

    def test_email_change_needs_the_code_from_the_address_it_moves_away_from(self):
        self.sign_in(self.user)
        sent = self.client.post("/api/account/email/reauth", json={},
                                headers=self.json_headers())
        self.assertEqual(sent.status_code, 200, sent.get_data(as_text=True)[:300])
        code = self.newest_code("reauth")

        missing = self.client.post("/api/account/email/change", json={
            "email": "moved@example.test", "current_password": PASSWORD},
            headers=self.json_headers())
        self.assertEqual(missing.status_code, 422)
        self.assertIn("email_code", missing.get_json()["error"]["fields"])
        stale = self.client.post("/api/account/email/change", json={
            "email": "moved@example.test", "current_password": PASSWORD,
            "email_code": "000000"}, headers=self.json_headers())
        self.assertEqual(stale.status_code, 410)
        self.assertEqual(self.user_row("player1")["email"], self.user["email"])

        done = self.client.post("/api/account/email/change", json={
            "email": "moved@example.test", "current_password": PASSWORD,
            "email_code": code}, headers=self.json_headers())
        self.assertEqual(done.status_code, 200, done.get_data(as_text=True)[:300])
        body = done.get_json()
        self.assertTrue(body["email_changed"])
        self.assertTrue(body["verification_pending"])
        row = self.user_row("player1")
        self.assertEqual(row["email"], self.user["email"])
        self.assertEqual(row["pending_email"], "moved@example.test")

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

    def test_signing_out_other_devices_keeps_this_one(self):
        self.accounts.create_session(self.user, ip="127.0.0.1", user_agent="other")
        self.sign_in(self.user)
        response = self.client.post("/api/account/sessions/revoke", json={"others": True},
                                    headers=self.json_headers())
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True)[:300])
        self.assertEqual(response.get_json()["revoked"], 1)
        self.assertFalse(response.get_json()["signed_out"])
        payload = self.client.get("/api/account").get_json()
        self.assertEqual([row["current"] for row in payload["sessions"]], [True])

    def test_the_page_is_complete_before_any_script_runs(self):
        # The four sections, the three popups and this account's details are in the
        # HTML the server sends. account.js only refreshes them after an action, so
        # a slowed or blocked script never leaves a page of empty shells.
        self.sign_in(self.user)
        page = self.client.get("/account").get_data(as_text=True)
        for heading in ("Sign-in details", "Linked services", "Devices",
                        "Delete this account", "Change password", "Reset password",
                        "Change email address", "Two-factor sign-in"):
            self.assertIn(heading, page)
        self.assertIn("@player1", page)
        self.assertIn(self.user["email"], page)
        self.assertIn("Player", page)
        self.assertNotIn("Loading&hellip;", page)

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

    # ----------------------------------------------------- linked services
    def test_disconnecting_youtube_deletes_the_row_and_its_artifacts(self):
        self.accounts.set_provider_credentials(
            self.user, self.user["id"], "youtube", {"headers": {"cookie": "c"}})
        calls = []
        self.accounts.set_artifact_hook(lambda user_id, provider: calls.append(
            (user_id, provider)) or [])
        self.sign_in(self.user)

        response = self.client.post("/api/account/providers/youtube/disconnect",
                                    json={}, headers=self.json_headers())
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True)[:300])
        body = response.get_json()
        self.assertTrue(body["complete"])
        self.assertIn("deleted the saved cookies", body["message"])
        self.assertEqual(self.accounts.provider_status(self.user["id"]), [])
        self.assertEqual(calls, [(self.user["id"], "youtube")])

    def test_a_disconnect_that_could_not_finish_says_so(self):
        self.accounts.set_provider_credentials(
            self.user, self.user["id"], "youtube", {"headers": {"cookie": "c"}})
        self.accounts.set_artifact_hook(
            lambda user_id, provider: ["browser profile: still open"])
        self.sign_in(self.user)

        response = self.client.post("/api/account/providers/youtube/disconnect",
                                    json={}, headers=self.json_headers())
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertFalse(body["complete"])
        self.assertIn("browser profile: still open", body["message"])
        # The row is gone either way; only the secrets on disk may remain.
        self.assertEqual(self.accounts.provider_status(self.user["id"]), [])

    def test_disconnecting_a_service_that_was_never_linked_says_so(self):
        # /account already shows "Not connected" for this provider. Reporting a
        # deletion would claim secrets were erased that the account never had.
        calls = []
        self.accounts.set_artifact_hook(lambda user_id, provider: calls.append(
            (user_id, provider)) or [])
        self.sign_in(self.user)

        response = self.client.post("/api/account/providers/youtube/disconnect",
                                    json={}, headers=self.json_headers())
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True)[:300])
        body = response.get_json()
        self.assertFalse(body["was_linked"])
        self.assertIn("Nothing was connected to YouTube Music", body["message"])
        self.assertNotIn("deleted the saved cookies", body["message"])
        # A stale file with no row behind it is still worth erasing, so the sweep
        # runs either way -- it is only the *message* that has to stop lying.
        self.assertEqual(calls, [(self.user["id"], "youtube")])
        row = self.layer.db.fetchall(
            "SELECT details_json FROM audit_log WHERE action = 'provider_disconnected' "
            "ORDER BY id DESC LIMIT 1"
        )[0]
        self.assertIn('"was_linked":false', row["details_json"])

    def test_a_browser_cannot_disconnect_another_account(self):
        other = self.accounts.create_user(
            self.user_row("owner"), "player2", PASSWORD, email="player2@example.test",
            role="user", status="active", email_verified=True)
        self.accounts.set_provider_credentials(
            other, other["id"], "amazon", {"cookies": "x"})
        self.sign_in(self.user)
        response = self.client.post(
            "/api/account/providers/amazon/disconnect",
            json={"user_id": other["id"]}, headers=self.json_headers())
        self.assertEqual(response.status_code, 200)
        # The endpoint only ever acts on the signed-in account, whatever the body says.
        self.assertEqual(len(self.accounts.provider_status(other["id"])), 1)

    def test_an_unknown_provider_is_refused(self):
        self.sign_in(self.user)
        response = self.client.post("/api/account/providers/spotify/disconnect",
                                    json={}, headers=self.json_headers())
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.get_json()["error"]["code"], "invalid_provider")

    def test_the_account_page_is_drawn_in_the_stored_palette(self):
        # The choice is made once, in the administrator panel's theme control, and
        # this page reads the same preference instead of offering a second switch.
        self.sign_in(self.user)
        self.assertIn('data-theme="dark"', self.client.get("/account").get_data(as_text=True))
        self.accounts.set_preference(self.user["id"], "admin_theme", "light")
        page = self.client.get("/account").get_data(as_text=True)
        self.assertIn('data-theme="light"', page)
        # A light page without these two paints dark form widgets and a dark bar.
        self.assertIn('name="color-scheme" content="light dark"', page)
        self.assertIn('name="theme-color" content="#f3f3f5"', page)

    def test_the_account_page_holds_no_palette_switch(self):
        self.sign_in(self.user)
        page = self.client.get("/account").get_data(as_text=True)
        self.assertNotIn("Switch to", page)
        # And no route behind one: the form that used to post here is gone, so a
        # browser that still has the old page cached gets a 404 rather than a
        # preference written by a control nobody can see.
        self.assertEqual(self.client.post("/account/theme", data={
            "_csrf": self.form_csrf("/account"), "state": "light"}).status_code, 404)


class TwoFactorTests(SelfServiceTestBase):
    def setUp(self):
        super().setUp()
        self.register("player1")
        self.verify_by_code()
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

    def _turn_two_factor_on(self):
        secret = self.client.post("/api/account/totp/begin", json={"current_password": PASSWORD},
                                  headers=self.json_headers()).get_json()["secret"]
        confirmed = self.client.post("/api/account/totp/confirm",
                                     json={"code": totp_at(secret, int(time.time()) // 30)},
                                     headers=self.json_headers())
        self.assertEqual(confirmed.status_code, 200, confirmed.get_data(as_text=True)[:300])
        return secret

    def test_enrollment_can_also_prove_the_mailbox_with_a_code(self):
        sent = self.client.post("/api/account/email/reauth", json={},
                                headers=self.json_headers())
        self.assertEqual(sent.status_code, 200, sent.get_data(as_text=True)[:300])
        bad = self.client.post("/api/account/totp/begin", json={
            "current_password": PASSWORD, "email_code": "000000"},
            headers=self.json_headers())
        self.assertEqual(bad.status_code, 410)
        self.assertIn("email_code", bad.get_json()["error"]["fields"])
        self.assertFalse(self.client.get("/api/account").get_json()["totp_pending"])

        good = self.client.post("/api/account/totp/begin", json={
            "current_password": PASSWORD, "email_code": self.newest_code("reauth")},
            headers=self.json_headers())
        self.assertEqual(good.status_code, 200, good.get_data(as_text=True)[:300])
        self.assertTrue(good.get_json()["secret"])
        self.assertTrue(self.client.get("/api/account").get_json()["totp_pending"])

    def test_turning_two_factor_off_needs_the_password_but_not_the_phone(self):
        self._turn_two_factor_on()
        off=self.client.post('/api/account/totp/disable',json={'current_password':PASSWORD},headers=self.json_headers())
        self.assertNotEqual(off.status_code,200)
        self.assertTrue(self.client.get('/api/account').json['totp_enabled'])
        recovery=self.accounts.create_email_code(self.user['id'],'reauth')
        off=self.client.post('/api/account/totp/disable',json={'current_password':PASSWORD,'email_code':recovery},headers=self.json_headers())
        self.assertEqual(off.status_code,200,off.get_data(as_text=True)[:300])
        self.assertFalse(self.client.get('/api/account').json['totp_enabled'])

    def test_turning_two_factor_off_accepts_an_emailed_code_too(self):
        self._turn_two_factor_on()
        sent = self.client.post("/api/account/email/reauth", json={},
                                headers=self.json_headers())
        self.assertEqual(sent.status_code, 200, sent.get_data(as_text=True)[:300])

        bad = self.client.post("/api/account/totp/disable",
                               json={"current_password": PASSWORD, "email_code": "000000"},
                               headers=self.json_headers())
        self.assertEqual(bad.status_code, 410)
        self.assertTrue(self.client.get("/api/account").get_json()["totp_enabled"])

        off = self.client.post("/api/account/totp/disable",
                               json={"current_password": PASSWORD,
                                     "email_code": self.newest_code("reauth")},
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
        self.verify_by_code()
        self.user = self.user_row("player1")
        self.sign_in(self.user)

    def emailed_code(self):
        sent = self.client.post("/api/account/email/reauth", json={},
                                headers=self.json_headers())
        self.assertEqual(sent.status_code, 200, sent.get_data(as_text=True)[:300])
        return self.newest_code("reauth")

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

    def test_the_first_popup_proves_identity_without_deleting(self):
        ok = self.client.post("/api/account/delete",
                              json={"password": PASSWORD, "confirm_username": "player1",
                                    "verify_only": True},
                              headers=self.json_headers())
        self.assertEqual(ok.status_code, 200, ok.get_data(as_text=True)[:300])
        self.assertIsNotNone(self.user_row("player1"))
        self.assertEqual(self.purged, [])

        bad = self.client.post("/api/account/delete",
                               json={"password": "nope", "confirm_username": "player1",
                                     "verify_only": True},
                               headers=self.json_headers())
        self.assertEqual(bad.status_code, 403)

    def test_an_address_on_file_is_a_second_proof(self):
        response = self.client.post("/api/account/delete",
                                    json={"password": PASSWORD, "confirm_username": "player1"},
                                    headers=self.json_headers())
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.get_json()["error"]["code"], "code_required")
        self.assertIn("email_code", response.get_json()["error"]["fields"])
        self.assertIsNotNone(self.user_row("player1"))

        self.emailed_code()
        wrong = self.client.post("/api/account/delete",
                                 json={"password": PASSWORD, "confirm_username": "player1",
                                       "email_code": "000000"},
                                 headers=self.json_headers())
        self.assertEqual(wrong.status_code, 410)
        self.assertEqual(wrong.get_json()["error"]["code"], "invalid_code")
        self.assertIsNotNone(self.user_row("player1"))

    def test_success_removes_the_rows_revokes_access_and_calls_the_purge_hook(self):
        code = self.emailed_code()
        other = self.accounts.create_session(self.user, ip="127.0.0.1", user_agent="phone")
        response = self.client.post("/api/account/delete",
                                    json={"password": PASSWORD, "confirm_username": "player1",
                                          "email_code": code},
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
                         json={"password": PASSWORD, "confirm_username": "player1",
                               "email_code": self.emailed_code()},
                         headers=self.json_headers())
        self.assertEqual(self.register("player1").status_code, 303)
        response = self.verify_by_code()
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
