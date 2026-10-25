"""Multi-user admin, email and account-service tests.

These tests exercise the additive layer in isolation: a fresh Flask app, a
temporary SQLite file, a local mail outbox and no network access. The existing
``server.py`` application is intentionally not imported.
"""
from __future__ import annotations

import email
import glob
import os
import re
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from cryptography.fernet import Fernet
from flask import Flask

from multiuser import MultiUserConfig, init_multiuser
from multiuser.accounts import AccountService
from multiuser.crypto import SecretBox, SecretBoxError
from multiuser.security import (
    RateLimiter,
    generate_password,
    hash_password,
    validate_password,
    verify_password,
)

TOKEN_RE = re.compile(r"/admin/(invite|reset-password|magic|verify-email)/([A-Za-z0-9_-]+)")


class MultiUserTestBase(unittest.TestCase):
    admin_password = "Owner!Passw0rd"

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="multiuser-test-")
        self.db_path = os.path.join(self.tmp, "data.db")
        self.outbox = os.path.join(self.tmp, "outbox")
        self.env = {
            "MULTI_USER_ENABLED": "1",
            "DB_FILE": self.db_path,
            "SECRET_KEY": "test-secret-key-for-signed-cookies",
            "MULTIUSER_ADMIN_USER": "owner",
            "MULTIUSER_ADMIN_PASSWORD": self.admin_password,
            "MULTIUSER_ADMIN_EMAIL": "owner@synthora.in",
            "MULTIUSER_ENCRYPTION_KEY": Fernet.generate_key().decode("ascii"),
            "MULTIUSER_MAIL_OUTBOX": self.outbox,
            "MULTIUSER_PUBLIC_BASE_URL": "http://localhost",
            "MULTIUSER_REQUIRE_EMAIL_VERIFICATION": "1",
            "MULTIUSER_RESET_TOKEN_MINUTES": "60",
            "MULTIUSER_MAGIC_TOKEN_MINUTES": "15",
        }
        self.env_patch = mock.patch.dict(os.environ, self.env, clear=False)
        self.env_patch.start()
        self.app = Flask(__name__)
        self.app.secret_key = "test-flask-session-secret"
        self.app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
        self.layer = init_multiuser(self.app)
        self.assertIsNotNone(self.layer)
        self.client = self.app.test_client()

    def tearDown(self):
        self.env_patch.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ----------------------------------------------------------------- helpers
    def csrf(self) -> str:
        with self.client.session_transaction() as session:
            return session.get("mu_csrf", "")

    def csrf_from(self, path: str, method: str = "get") -> str:
        response = getattr(self.client, method)(path)
        self.assertIn(response.status_code, (200, 302, 400, 401, 403, 404, 410))
        return self.csrf()

    def login(self, username: str = "owner", password: str | None = None) -> None:
        token = self.csrf_from("/admin/login/")
        response = self.client.post(
            "/admin/login/",
            data={"_csrf": token, "username": username, "password": password or self.admin_password},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302, response.get_data(as_text=True)[:400])
        self.assertTrue(response.headers["Location"].startswith("/admin/"))

    def login_failure(self, username: str, password: str) -> int:
        token = self.csrf_from("/admin/login/")
        response = self.client.post(
            "/admin/login/",
            data={"_csrf": token, "username": username, "password": password},
        )
        return response.status_code

    def outbox_messages(self) -> list[str]:
        return sorted(glob.glob(os.path.join(self.outbox, "*.eml")))

    def newest_outbox_token(self, purpose: str) -> str:
        paths = self.outbox_messages()
        self.assertTrue(paths, "expected an outbox message")
        path = paths[-1]
        with open(path, "rb") as handle:
            message = email.message_from_bytes(handle.read())
        body = ""
        for part in message.walk():
            if part.get_content_type() == "text/plain":
                payload = part.get_payload(decode=True)
                body = (payload or b"").decode("utf-8", "replace")
                break
        match = TOKEN_RE.search(body)
        self.assertIsNotNone(match, body)
        self.assertEqual(match.group(1), purpose)
        return match.group(2)

    def create_admin(self, username: str, email: str, *, invite: bool = False) -> None:
        token = self.csrf()
        data = {
            "_csrf": token,
            "username": username,
            "display_name": username.title(),
            "email": email,
            "role": "admin",
            "password": "Temp!Passw0rd9",
            "must_change_password": "on",
            "send_verification": "" if invite else "on",
            "send_invite": "on" if invite else "",
        }
        response = self.client.post("/admin/users/new/", data=data)
        self.assertEqual(response.status_code, 302, response.get_data(as_text=True)[:500])


class AdminAuthTests(MultiUserTestBase):
    def test_unauthenticated_admin_redirects_to_login(self):
        response = self.client.get("/admin/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin/login/", response.headers["Location"])

    def test_login_requires_csrf(self):
        response = self.client.post(
            "/admin/login/", data={"username": "owner", "password": self.admin_password}
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn(b"expired", response.data.lower())

    def test_login_dashboard_logout(self):
        self.assertEqual(self.login_failure("owner", "wrong-password"), 401)
        self.login()
        dashboard = self.client.get("/admin/")
        self.assertEqual(dashboard.status_code, 200)
        self.assertIn(b"Dashboard", dashboard.data)
        self.assertIn(b"@owner", dashboard.data)

        token = self.csrf()
        logout = self.client.post("/admin/logout/", data={"_csrf": token})
        self.assertEqual(logout.status_code, 302)
        self.assertEqual(self.client.get("/admin/").status_code, 302)

    def test_successful_login_clears_rate_limit_budget(self):
        for _ in range(3):
            self.login()
            token = self.csrf()
            self.client.post("/admin/logout/", data={"_csrf": token})
        # The per-account budget must not lock out a legitimate repeated user.
        self.login()
        self.assertEqual(self.client.get("/admin/").status_code, 200)

    def test_admin_health_endpoint_is_public_and_minimal(self):
        response = self.client.get("/admin/api/health/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["service"], "musicbox-multiuser")


class UserManagementTests(MultiUserTestBase):
    def setUp(self):
        super().setUp()
        self.login()

    def test_create_edit_and_delete_user(self):
        response = self.client.post(
            "/admin/users/new/",
            data={
                "_csrf": self.csrf(),
                "username": "listener",
                "display_name": "Listener One",
                "email": "listener@example.com",
                "role": "user",
                "password": "Listener!Pass9",
                "must_change_password": "on",
            },
        )
        self.assertEqual(response.status_code, 302)
        detail_url = response.headers["Location"]
        detail = self.client.get(detail_url)
        self.assertEqual(detail.status_code, 200)
        self.assertIn(b"listener@example.com", detail.data)

        user = self.layer.accounts.get_user_by_username("listener")
        self.assertIsNotNone(user)
        self.assertFalse(user["email_verified"])

        edit = self.client.post(
            f"/admin/users/{user['id']}/edit/",
            data={
                "_csrf": self.csrf(),
                "username": "listener",
                "display_name": "Renamed Listener",
                "email": "listener@example.com",
                "role": "user",
                "status": "active",
            },
        )
        self.assertEqual(edit.status_code, 302)
        self.assertEqual(
            self.layer.accounts.get_user(user["id"])["display_name"], "Renamed Listener"
        )

        delete = self.client.post(
            f"/admin/users/{user['id']}/delete/", data={"_csrf": self.csrf()}
        )
        self.assertEqual(delete.status_code, 302)
        self.assertEqual(self.layer.accounts.get_user(user["id"]), {})
        self.assertEqual(self.layer.accounts.get_user_by_username("listener"), {})

    def test_cannot_demote_or_disable_the_last_owner(self):
        owner = self.layer.accounts.get_user_by_username("owner")
        response = self.client.post(
            f"/admin/users/{owner['id']}/status/",
            data={"_csrf": self.csrf(), "status": "disabled"},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"cannot disable your own account", response.data.lower())
        self.assertEqual(self.layer.accounts.get_user(owner["id"])["status"], "active")

    def test_owner_can_update_own_profile_with_password_confirmation(self):
        owner = self.layer.accounts.get_user_by_username("owner")
        denied = self.client.post(
            "/admin/account/profile/",
            data={
                "_csrf": self.csrf(),
                "display_name": "Owner Renamed",
                "email": "new-owner@synthora.in",
                "current_password": "wrong-password",
            },
            follow_redirects=True,
        )
        self.assertEqual(denied.status_code, 200)
        self.assertIn(b"current password", denied.data.lower())
        self.assertEqual(self.layer.accounts.get_user(owner["id"])["email"], "owner@synthora.in")

        changed = self.client.post(
            "/admin/account/profile/",
            data={
                "_csrf": self.csrf(),
                "display_name": "Owner Renamed",
                "email": "new-owner@synthora.in",
                "current_password": self.admin_password,
            },
        )
        self.assertEqual(changed.status_code, 302)
        updated = self.layer.accounts.get_user(owner["id"])
        self.assertEqual(updated["display_name"], "Owner Renamed")
        self.assertEqual(updated["email"], "new-owner@synthora.in")
        self.assertFalse(updated["email_verified"])

    def test_audit_page_and_csv_export(self):
        page = self.client.get("/admin/audit/")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"Audit log", page.data)
        export = self.client.get("/admin/audit/export.csv")
        self.assertEqual(export.status_code, 200)
        self.assertTrue(export.data.startswith(b"timestamp_utc,actor,action"))
        self.assertIn(b"admin_login", export.data)

    def test_system_page_reports_mail_outbox(self):
        page = self.client.get("/admin/system/")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"Email delivery", page.data)
        self.assertIn(b"outbox only", page.data)
        self.assertIn(b"written as", page.data)
        self.assertIn(b".eml", page.data)

    def test_server_side_session_can_be_revoked(self):
        import hashlib

        with self.client.session_transaction() as session:
            raw_sid = session["mu_sid"]
        self.layer.accounts.revoke_session(hashlib.sha256(raw_sid.encode()).hexdigest())
        response = self.client.get("/admin/")
        self.assertEqual(response.status_code, 302)


class EmailFlowTests(MultiUserTestBase):
    def setUp(self):
        super().setUp()
        self.login()

    def test_unverified_admin_cannot_sign_in_until_email_is_confirmed(self):
        self.create_admin("verify-me", "verify@example.com")
        # New session: remove this browser's owner login.
        token = self.csrf()
        self.client.post("/admin/logout/", data={"_csrf": token})
        self.assertEqual(self.login_failure("verify-me", "Temp!Passw0rd9"), 401)

        self.login()
        user = self.layer.accounts.get_user_by_username("verify-me")
        response = self.client.post(
            f"/admin/users/{user['id']}/email/verification/",
            data={"_csrf": self.csrf()},
        )
        self.assertEqual(response.status_code, 302)
        token = self.newest_outbox_token("verify-email")
        verified = self.client.get(f"/admin/verify-email/{token}")
        self.assertEqual(verified.status_code, 200)
        self.assertIn(b"Email verified", verified.data)
        self.assertTrue(self.layer.accounts.get_user(user["id"])["email_verified"])

        token = self.csrf()
        self.client.post("/admin/logout/", data={"_csrf": token})
        self.login("verify-me", "Temp!Passw0rd9")
        self.assertEqual(self.client.get("/admin/").status_code, 200)

    def test_invitation_sets_password_and_activates_account(self):
        self.create_admin("invited", "invited@example.com", invite=True)
        invited = self.layer.accounts.get_user_by_username("invited")
        self.assertEqual(invited["status"], "invited")

        token = self.newest_outbox_token("invite")
        page = self.client.get(f"/admin/invite/{token}")
        self.assertEqual(page.status_code, 200)
        response = self.client.post(
            f"/admin/invite/{token}",
            data={
                "_csrf": self.csrf(),
                "password": "Activated!Pass9",
                "confirm_password": "Activated!Pass9",
            },
        )
        self.assertEqual(response.status_code, 302)
        invited = self.layer.accounts.get_user(invited["id"])
        self.assertEqual(invited["status"], "active")
        self.assertTrue(invited["email_verified"])

        token = self.csrf()
        self.client.post("/admin/logout/", data={"_csrf": token})
        self.login("invited", "Activated!Pass9")
        self.assertEqual(self.client.get("/admin/").status_code, 200)

    def test_password_reset_via_email(self):
        token = self.csrf()
        self.client.post("/admin/logout/", data={"_csrf": token})
        page = self.client.get("/admin/forgot-password/")
        self.assertEqual(page.status_code, 200)
        response = self.client.post(
            "/admin/forgot-password/",
            data={"_csrf": self.csrf(), "identifier": "owner@synthora.in"},
        )
        self.assertEqual(response.status_code, 302)
        reset_token = self.newest_outbox_token("reset-password")
        form = self.client.get(f"/admin/reset-password/{reset_token}")
        self.assertEqual(form.status_code, 200)
        reset = self.client.post(
            f"/admin/reset-password/{reset_token}",
            data={
                "_csrf": self.csrf(),
                "password": "Reset!Passw0rd2",
                "confirm_password": "Reset!Passw0rd2",
            },
        )
        self.assertEqual(reset.status_code, 302)
        self.login("owner", "Reset!Passw0rd2")
        self.assertEqual(self.client.get("/admin/").status_code, 200)

    def test_magic_link_sign_in(self):
        token = self.csrf()
        self.client.post("/admin/logout/", data={"_csrf": token})
        page = self.client.get("/admin/email-login/")
        self.assertEqual(page.status_code, 200)
        response = self.client.post(
            "/admin/email-login/",
            data={"_csrf": self.csrf(), "identifier": "owner@synthora.in"},
        )
        self.assertEqual(response.status_code, 302)
        magic = self.newest_outbox_token("magic")
        signed_in = self.client.get(f"/admin/magic/{magic}")
        self.assertEqual(signed_in.status_code, 302)
        self.assertEqual(self.client.get("/admin/").status_code, 200)

    def test_expired_or_forged_tokens_are_rejected(self):
        self.assertEqual(self.client.get("/admin/magic/not-a-real-token").status_code, 410)
        self.assertEqual(self.client.get("/admin/verify-email/not-a-real-token").status_code, 410)
        self.assertEqual(self.client.get("/admin/reset-password/not-a-real-token").status_code, 410)
        self.assertEqual(self.client.get("/admin/invite/not-a-real-token").status_code, 410)

    def test_email_token_cannot_survive_an_address_change(self):
        owner = self.layer.accounts.get_user_by_username("owner")
        token = self.layer.accounts.create_email_token(owner["id"], "reset", owner["email"])
        self.layer.accounts.update_user(owner, owner["id"], {"email": "new@synthora.in"})
        self.assertEqual(self.layer.accounts.peek_email_token(token, "reset"), {})
        self.assertEqual(self.client.get(f"/admin/reset-password/{token}").status_code, 410)


class ServiceTests(MultiUserTestBase):
    def test_password_hashing_and_validation(self):
        digest = hash_password("Valid!Passw0rd1")
        self.assertTrue(verify_password(digest, "Valid!Passw0rd1"))
        self.assertFalse(verify_password(digest, "wrong"))
        self.assertIsNone(validate_password("Valid!Passw0rd1", "user"))
        self.assertIsNotNone(validate_password("short", "user"))
        self.assertIsNotNone(validate_password("alllowercase", "user"))
        generated = generate_password()
        self.assertIsNone(validate_password(generated, "user"))

    def test_provider_credentials_are_encrypted_and_scoped(self):
        owner = self.layer.accounts.get_user_by_username("owner")
        secret_payload = {"headers": {"Authorization": "Bearer super-secret-cookie"}}
        self.layer.accounts.set_provider_credentials(owner, owner["id"], "youtube", secret_payload)
        row = self.layer.db.fetchone(
            "SELECT secret_enc, metadata_json FROM user_provider_credentials "
            "WHERE user_id = ? AND provider = 'youtube'",
            (owner["id"],),
        )
        self.assertIsNotNone(row)
        self.assertNotIn("super-secret-cookie", row["secret_enc"])
        self.assertEqual(self.layer.secret_box.decrypt_json(row["secret_enc"]), secret_payload)
        self.assertEqual(
            self.layer.accounts.provider_status(owner["id"])[0]["provider"], "youtube"
        )
        self.layer.accounts.clear_provider_credentials(owner, owner["id"], "youtube")
        self.assertEqual(self.layer.accounts.provider_status(owner["id"]), [])

    def test_secret_box_rejects_a_bad_key(self):
        with self.assertRaises(SecretBoxError):
            SecretBox("not-a-fernet-key")

    def test_rate_limiter_is_bounded_and_resettable(self):
        limiter = RateLimiter()
        for _ in range(3):
            self.assertTrue(limiter.allow("login", "user", 3, 60)[0])
        allowed, retry = limiter.allow("login", "user", 3, 60)
        self.assertFalse(allowed)
        self.assertGreaterEqual(retry, 1)
        limiter.reset("login", "user")
        self.assertTrue(limiter.allow("login", "user", 3, 60)[0])

    def test_config_is_disabled_by_default(self):
        with mock.patch.dict(os.environ, {"MULTI_USER_ENABLED": "0"}, clear=False):
            config = MultiUserConfig.from_env()
            self.assertFalse(config.enabled)

    def test_disabled_layer_registers_no_routes(self):
        with mock.patch.dict(os.environ, {"MULTI_USER_ENABLED": "0"}, clear=False):
            app = Flask(__name__)
            app.secret_key = "disabled-layer-secret"
            self.assertIsNone(init_multiuser(app))
            admin_rules = [
                str(rule) for rule in app.url_map.iter_rules() if str(rule).startswith("/admin")
            ]
            self.assertEqual(admin_rules, [])
            self.assertNotIn("multiuser", app.extensions)

    def test_backup_creates_a_readable_copy(self):
        destination = os.path.join(self.tmp, "backup.db")
        self.layer.db.backup_to(destination)
        self.assertTrue(os.path.isfile(destination))
        import sqlite3

        with sqlite3.connect(destination) as conn:
            self.assertGreaterEqual(
                conn.execute("SELECT COUNT(*) FROM users").fetchone()[0], 1
            )


if __name__ == "__main__":
    unittest.main()
