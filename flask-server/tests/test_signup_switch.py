"""The owner's public sign-up switch: closes every new-account door, strands nobody.

The switch is one policy read by the login page, the sign-up page and the POST,
so these tests check the consequences rather than the implementation: a closed
door says which kind of closed it is, refuses to create anything, still lets an
account created a minute earlier confirm its address, and cannot be opened when
the deployment's environment says no.
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
from multiuser.accounts import AccountError, SIGNUP_SWITCH_KEY

CODE_RE = re.compile(r"Your code is (\d{6})")
PASSWORD = "Player!Passw0rd"


class SignupSwitchTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="signup-switch-test-")
        self.outbox = os.path.join(self.tmp, "outbox")
        self.env = {
            "MULTI_USER_ENABLED": "1",
            "DB_FILE": os.path.join(self.tmp, "data.db"),
            "SECRET_KEY": "test-secret-key-for-signup-switch",
            "MULTIUSER_ADMIN_USER": "owner",
            "MULTIUSER_ADMIN_PASSWORD": "Owner!Passw0rd",
            "MULTIUSER_ADMIN_EMAIL": "owner@synthora.in",
            "MULTIUSER_ENCRYPTION_KEY": Fernet.generate_key().decode("ascii"),
            "MULTIUSER_MAIL_OUTBOX": self.outbox,
            # Blank, not unset: a deployed machine has live SMTP and would deliver
            # every code over the network instead of into the outbox we read.
            "MULTIUSER_SMTP_HOST": "",
            "MULTIUSER_SMTP_USER": "",
            "MULTIUSER_MAIL_FROM": "",
            "MULTIUSER_PUBLIC_BASE_URL": "http://localhost",
            "MULTIUSER_EMAIL_CODE_MINUTES": "15",
            "MULTIUSER_EMAIL_CODE_ATTEMPTS": "5",
            "MULTIUSER_SELF_SIGNUP_ENABLED": "1",
        }
        self.env_patch = mock.patch.dict(os.environ, self.env, clear=False)
        self.env_patch.start()
        self.app = Flask(__name__)
        self.app.secret_key = "test-flask-session-secret"
        self.app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
        self.layer = init_multiuser(self.app)
        self.assertIsNotNone(self.layer)
        self.layer.db.execute("UPDATE users SET must_change_password=0 WHERE created_by='system'")
        self.accounts = self.layer.accounts
        self.client = self.app.test_client()
        self.owner = self.accounts.get_user_by_username("owner")

    def tearDown(self):
        self.env_patch.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ----------------------------------------------------------------- helpers
    def csrf(self) -> str:
        with self.client.session_transaction() as session:
            return session.get("mu_csrf", "")

    def login(self, username: str = "owner", password: str = "Owner!Passw0rd") -> None:
        self.client.get("/admin/login/")
        response = self.client.post(
            "/admin/login/",
            data={"_csrf": self.csrf(), "username": username, "password": password},
        )
        self.assertEqual(response.status_code, 302, response.get_data(as_text=True)[:300])

    def logout(self) -> None:
        self.client.post("/admin/logout/", data={"_csrf": self.csrf()})

    def set_door(self, open_it: bool):
        return self.client.post(
            "/admin/users/signup/",
            data={"_csrf": self.csrf(), "state": "enable" if open_it else "disable"},
        )

    def outbox_bodies(self) -> list[str]:
        bodies: list[str] = []
        for path in sorted(glob.glob(os.path.join(self.outbox, "*.eml"))):
            with open(path, "rb") as handle:
                message = email.message_from_bytes(handle.read())
            for part in message.walk():
                if part.get_content_type() == "text/plain":
                    payload = part.get_payload(decode=True)
                    bodies.append((payload or b"").decode("utf-8", "replace"))
                    break
        return bodies

    def newest_code(self) -> str:
        found: list[str] = []
        for body in self.outbox_bodies():
            if "Verify your email address" in body:
                found.extend(CODE_RE.findall(body))
        self.assertTrue(found, "expected a verification code in the outbox")
        return found[-1]

    def open_signup_form(self, age_seconds: float = 30.0) -> str:
        """Load the form and let it age past the minimum fill time."""
        self.client.get("/signup")
        with self.client.session_transaction() as session:
            nonce = session.get("mu_ss_signup")
            self.assertTrue(nonce, "the form must issue its own nonce")
            session["mu_ss_signup"] = (nonce[0], time.time() - age_seconds)
            return session.get("mu_ss_csrf", "")

    def ss_csrf(self) -> str:
        self.client.get("/verify")
        with self.client.session_transaction() as session:
            return session.get("mu_ss_csrf", "")

    def pending_signup(self, username: str = "pending"):
        """An unverified account from before email-first signup was introduced."""
        user = self.accounts.register_self_service(username=username, password=PASSWORD,
                                                   email=f"{username}@example.test")
        code = self.accounts.create_email_code(user["id"], "verify")
        return user, code


class SignupSwitchTests(SignupSwitchTestBase):
    def test_the_owner_closes_and_reopens_every_door_together(self):
        self.login()
        # The dashboard reports the door; the switch itself sits on the Users
        # page, next to the list of people who came through it.
        dashboard = self.client.get("/admin/")
        self.assertIn(b"Public sign-ups", dashboard.data)
        self.assertIn(b"Anyone with an email address can create an account", dashboard.data)
        self.assertIn(b"Close sign-ups", self.client.get("/admin/users/").data)

        response = self.set_door(False)
        self.assertEqual(response.status_code, 302)
        self.assertFalse(self.accounts.registration_open())
        self.assertTrue(self.accounts.system_setting_row(SIGNUP_SWITCH_KEY))

        self.assertIn(b"Open sign-ups", self.client.get("/admin/users/").data)
        self.assertIn(b"Closed by", self.client.get("/admin/").data)
        self.set_door(True)
        self.assertTrue(self.accounts.registration_open())

    def test_the_closed_door_says_an_administrator_closed_it(self):
        self.login()
        self.set_door(False)

        page = self.client.get("/signup")
        self.assertEqual(page.status_code, 503)
        self.assertIn(b"switched public sign-ups off", page.data)

        # The POST answers the same way, before it looks at any form field: there
        # is no half-made account and no "your form expired" misdirection.
        posted = self.client.post(
            "/signup",
            data={"_csrf": self.ss_csrf(),
                  "username": "someone", "email": "someone@example.test",
                  "password": PASSWORD, "confirm_password": PASSWORD},
        )
        self.assertEqual(posted.status_code, 503)
        self.assertIn(b"switched public sign-ups off", posted.data)
        self.assertEqual(self.accounts.get_user_by_username("someone"), {})

    def test_the_sign_up_door_is_open_before_it_is_closed(self):
        response = self.client.get("/signup")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Create an account", response.data)

    def test_a_pending_account_can_still_confirm_while_the_door_is_shut(self):
        user, code = self.pending_signup()
        self.login()
        self.set_door(False)

        verified = self.client.post(
            "/verify",
            data={"_csrf": self.ss_csrf(), "username": "pending", "code": code},
        )
        self.assertEqual(verified.status_code, 200)
        self.assertIn(b"Email confirmed", verified.data)
        self.assertTrue(self.accounts.get_user(user["id"])["email_verified"])

    def test_resend_still_works_for_a_pending_account_while_the_door_is_shut(self):
        user, _ = self.pending_signup()
        # The resend door throttles to one code a minute; waiting that out in a
        # test would prove nothing, so age the signup's own token instead.
        self.accounts.db.execute(
            "UPDATE user_email_tokens SET created_at = ? WHERE user_id = ? AND purpose = 'verify'",
            (time.time() - 120, user["id"]),
        )
        self.login()
        self.set_door(False)

        before = len(self.outbox_bodies())
        resent = self.client.post(
            "/resend-code", data={"_csrf": self.ss_csrf(), "identifier": "pending"}
        )
        self.assertEqual(resent.status_code, 200)
        self.assertIn(b"fresh six-digit code", resent.data)
        self.assertGreater(len(self.outbox_bodies()), before)
        self.assertNotEqual(self.newest_code(), "")

    def test_forgot_password_keeps_working_for_existing_accounts(self):
        self.accounts.create_user(
            self.owner, "recover", PASSWORD, email="recover@example.test",
            role="user", status="active", email_verified=True,
        )
        self.login()
        self.set_door(False)
        self.client.get("/forgot-password")
        response = self.client.post(
            "/forgot-password",
            data={"_csrf": self.ss_csrf(), "identifier": "recover@example.test"},
        )
        self.assertEqual(response.status_code, 200)

    def test_closing_changes_no_existing_account(self):
        user, _ = self.pending_signup()
        before = dict(self.accounts.get_user(user["id"]))
        self.login()
        self.set_door(False)
        after = self.accounts.get_user(user["id"])
        for field in ("role", "status", "email", "email_verified"):
            self.assertEqual(after[field], before[field])

    def test_a_non_owner_cannot_change_it(self):
        user = self.accounts.create_user(
            self.owner, "helper", "Helper!Passw0rd", email="helper@example.test",
            role="user", status="active", email_verified=True,
        )
        raw = self.accounts.create_session(user)
        with self.client.session_transaction() as state:
            state["mu_sid"] = raw
        self.client.get("/admin/login/")
        response = self.set_door(False)
        self.assertEqual(response.status_code, 403)
        self.assertTrue(self.accounts.registration_open())
        with self.assertRaises(AccountError):
            self.accounts.set_signup_switch(user, False)

    def test_the_environment_is_the_ceiling(self):
        with mock.patch.object(self.accounts.config, "self_signup_enabled", False):
            with self.assertRaises(AccountError):
                self.accounts.set_signup_switch(self.owner, True)
            self.assertFalse(self.accounts.registration_open())
            self.assertEqual(self.accounts.signup_policy()["reason"], "environment")
            self.login()
            page = self.client.get("/admin/system/")
            self.assertIn(b"MULTIUSER_SELF_SIGNUP_ENABLED=0", page.data)

    def test_the_daily_cap_is_not_the_administrators_switch(self):
        with mock.patch.object(self.accounts.config, "max_self_signups_per_day", 1):
            self.pending_signup()
            policy = self.accounts.signup_policy()
            self.assertEqual(policy["reason"], "daily_cap")
            self.assertFalse(policy["open"])
            page = self.client.get("/signup")
            self.assertEqual(page.status_code, 503)
            self.assertIn(b"Try again tomorrow", page.data)

    def test_the_system_page_explains_the_door_without_moving_it(self):
        self.login()
        page = self.client.get("/admin/system/")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"Public sign-ups", page.data)
        # One switch, on the Users page. A second control here would let two
        # pages disagree about which one the owner just clicked.
        self.assertIn(b"/admin/users/", page.data)
        self.assertNotIn(b"/system/signup/", page.data)
        self.assertNotIn(b"/users/signup/", page.data)

    def test_the_users_page_carries_the_switch_and_its_dialog(self):
        self.login()
        page = self.client.get("/admin/users/")
        self.assertIn(b"Public sign-ups", page.data)
        self.assertIn(b"Close public sign-ups", page.data)
        self.assertIn(b"data-confirm-button", page.data)

    def test_the_change_is_audited_with_before_and_after(self):
        self.login()
        self.set_door(False)
        rows, _ = self.accounts.list_audit(action="system_setting_changed")
        self.assertTrue(rows)
        self.assertIn("signup_enabled", rows[0].get("details_json") or "")


if __name__ == "__main__":
    unittest.main()
