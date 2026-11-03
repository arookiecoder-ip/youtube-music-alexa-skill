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
import time
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
    totp_at,
    validate_password,
    verify_password,
)

# An invitation is `/invite/<token>` on the public door and the administrator
# sign-in link is `/admin/magic/<token>`; the path prefix is not part of the match
# because which door a token belongs to is the thing under test, not the shape.
TOKEN_RE = re.compile(r"/(invite|magic)/([A-Za-z0-9_-]+)")
CODE_RE = re.compile(r"Your code is (\d{6})")
CODE_HEADINGS = {
    "verify": "Verify your email address",
    "reset": "Reset your Music Box password",
}
# The credential-in-the-URL shape these replaced. A code email is free to *name*
# the page the code goes on -- what must never appear is a URL that carries a
# token, because that is a credential a mail reader can follow unseen.
TOKEN_URL_RE = re.compile(r"/(?:verify-email|reset-password)/[A-Za-z0-9_-]{16,}")


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
            # Blanked, not merely unset: a deployed machine has real MULTIUSER_SMTP_*
            # in its environment, and with those set the mailer delivers over the
            # network and never writes the outbox these tests read.
            "MULTIUSER_SMTP_HOST": "",
            "MULTIUSER_SMTP_USER": "",
            "MULTIUSER_MAIL_FROM": "",
            "MULTIUSER_PUBLIC_BASE_URL": "http://localhost",
            "MULTIUSER_REQUIRE_EMAIL_VERIFICATION": "1",
            "MULTIUSER_EMAIL_CODE_MINUTES": "15",
            "MULTIUSER_EMAIL_CODE_ATTEMPTS": "5",
            "MULTIUSER_MAGIC_TOKEN_MINUTES": "15",
        }
        self.env_patch = mock.patch.dict(os.environ, self.env, clear=False)
        self.env_patch.start()
        self.app = Flask(__name__)
        self.app.secret_key = "test-flask-session-secret"
        self.app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
        self.layer = init_multiuser(self.app)
        self.assertIsNotNone(self.layer)
        self.layer.db.execute("UPDATE users SET must_change_password=0 WHERE created_by='system'")
        self.client = self.app.test_client()

    def tearDown(self):
        self.env_patch.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ----------------------------------------------------------------- helpers
    def csrf(self) -> str:
        with self.client.session_transaction() as session:
            return session.get("mu_csrf", "")

    def ss_csrf(self, path: str = "/verify") -> str:
        """A page token for the *player* door, which keeps one of its own.

        The two doors rotate independently so an administrator using /admin in the
        same browser cannot invalidate a player's half-filled account form.
        """
        self.client.get(path)
        with self.client.session_transaction() as session:
            return session.get("mu_ss_csrf", "")

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

    def outbox_bodies(self) -> list[str]:
        """The text part of every message written so far, oldest first."""
        bodies: list[str] = []
        for path in self.outbox_messages():
            with open(path, "rb") as handle:
                message = email.message_from_bytes(handle.read())
            for part in message.walk():
                if part.get_content_type() == "text/plain":
                    payload = part.get_payload(decode=True)
                    bodies.append((payload or b"").decode("utf-8", "replace"))
                    break
        return bodies

    def newest_outbox_token(self, purpose: str) -> str:
        """The last *link* of this purpose: invitation or administrator sign-in.

        Verification and reset are typed codes and have no link to find, so this
        deliberately does not search for them.
        """
        paths = self.outbox_messages()
        self.assertTrue(paths, "expected an outbox message")
        found: list[str] = []
        for body in self.outbox_bodies():
            found.extend(
                match.group(2) for match in TOKEN_RE.finditer(body) if match.group(1) == purpose
            )
        self.assertTrue(found, f"no {purpose} link in the outbox")
        return found[-1]

    def newest_outbox_code(self, purpose: str) -> str:
        """The six digits from the newest code email of this purpose."""
        heading = CODE_HEADINGS[purpose]
        found: list[str] = []
        for body in self.outbox_bodies():
            if heading in body:
                found.extend(match.group(1) for match in CODE_RE.finditer(body))
        self.assertTrue(found, f"no {purpose} code in the outbox")
        return found[-1]

    def assert_no_token_in_any_email(self) -> None:
        """No emitted message carries a credential in its URL.

        The point of the code change is that a stranger reading a mailbox cannot
        click their way into an account, so this is the whole feature in one rule.
        """
        for body in self.outbox_bodies():
            self.assertIsNone(
                TOKEN_URL_RE.search(body),
                f"a token-bearing link leaked into an email: {TOKEN_URL_RE.search(body)}",
            )

    def create_admin(self, username: str, email: str, *, invite: bool = False) -> None:
        token = self.csrf()
        data = {
            "_csrf": token,
            "username": username,
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
        # The dashboard answers "is anything broken?" rather than re-listing users:
        # the tiles, the attention list and the sign-up policy live here now.
        self.assertIn(b"Needs attention", dashboard.data)
        self.assertIn(b"Public sign-ups", dashboard.data)

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

    def test_an_active_account_cannot_be_sent_back_to_invited(self):
        # The status dropdown used to offer "invited" for every account. Choosing
        # it locked a working person out -- authenticate refuses anything that is
        # not active -- while email_verified stayed 1, which is a row claiming to
        # be both verified and never activated.
        self.client.post(
            "/admin/users/new/",
            data={
                "_csrf": self.csrf(),
                "username": "settled",
                "email": "settled@example.com",
                "role": "user",
                "password": "Settled!Pass9",
            },
        )
        user = self.layer.accounts.get_user_by_username("settled")
        self.assertEqual(user["status"], "active")

        detail = self.client.get(f"/admin/users/{user['id']}/")
        self.assertEqual(detail.status_code, 200)
        self.assertNotIn(b'value="invited"', detail.data)

        response = self.client.post(
            f"/admin/users/{user['id']}/edit/",
            data={
                "_csrf": self.csrf(),
                "username": "settled",
                "email": "settled@example.com",
                "role": "user",
                "status": "invited",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"cannot be set back to invited", response.data)
        self.assertEqual(self.layer.accounts.get_user(user["id"])["status"], "active")

    def test_an_activated_account_can_still_be_disabled_and_reenabled(self):
        self.client.post(
            "/admin/users/new/",
            data={
                "_csrf": self.csrf(),
                "username": "paused",
                "email": "paused@example.com",
                "role": "user",
                "password": "Paused!Pass9",
            },
        )
        user = self.layer.accounts.get_user_by_username("paused")
        disabled = self.client.post(
            f"/admin/users/{user['id']}/edit/",
            data={
                "_csrf": self.csrf(),
                "username": "paused",
                "email": "paused@example.com",
                "role": "user",
                "status": "disabled",
            },
        )
        self.assertEqual(disabled.status_code, 302)
        self.assertEqual(self.layer.accounts.get_user(user["id"])["status"], "disabled")

        detail = self.client.get(f"/admin/users/{user['id']}/")
        self.assertIn(b'value="active"', detail.data)
        enabled = self.client.post(
            f"/admin/users/{user['id']}/edit/",
            data={
                "_csrf": self.csrf(),
                "username": "paused",
                "email": "paused@example.com",
                "role": "user",
                "status": "active",
            },
        )
        self.assertEqual(enabled.status_code, 302)
        self.assertEqual(self.layer.accounts.get_user(user["id"])["status"], "active")

    def test_create_edit_and_delete_user(self):
        response = self.client.post(
            "/admin/users/new/",
            data={
                "_csrf": self.csrf(),
                "username": "listener",
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
                "username": "renamed",
                "email": "listener@example.com",
                "role": "user",
                "status": "active",
            },
        )
        self.assertEqual(edit.status_code, 302)
        # One name per account, so an administrator's rename is a username rename.
        self.assertEqual(self.layer.accounts.get_user(user["id"])["username"], "renamed")
        self.assertEqual(self.layer.accounts.get_user_by_username("listener"), {})

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

    def test_disconnect_reports_whether_anything_was_linked(self):
        # Telling an admin a deletion happened on an account that never connected
        # the service is the same invented status the panel used to show.
        self.client.post(
            "/admin/users/new/",
            data={
                "_csrf": self.csrf(),
                "username": "unlinked",
                "email": "unlinked@example.com",
                "role": "user",
                "password": "Unlinked!Pass9",
            },
        )
        user = self.layer.accounts.get_user_by_username("unlinked")

        empty = self.client.post(
            f"/admin/users/{user['id']}/provider/youtube/disconnect/",
            data={"_csrf": self.csrf()},
            follow_redirects=True,
        )
        self.assertIn(b"nothing was connected to youtube music", empty.data.lower())
        self.assertNotIn(b"were deleted", empty.data.lower())

        self.layer.accounts.set_provider_credentials(
            user, user["id"], "youtube", {"headers": {"cookie": "c"}})
        linked = self.client.post(
            f"/admin/users/{user['id']}/provider/youtube/disconnect/",
            data={"_csrf": self.csrf()},
            follow_redirects=True,
        )
        self.assertIn(b"sign-in was removed", linked.data.lower())
        self.assertEqual(self.layer.accounts.provider_status(user["id"]), [])

    def test_owner_can_update_own_profile_with_password_confirmation(self):
        owner=self.layer.accounts.get_user_by_username('owner')
        code=self.layer.accounts.create_email_code(owner['id'],'reauth')
        changed=self.client.post('/admin/account/profile/',data={'_csrf':self.csrf(),'email':'new-owner@synthora.in','current_password':self.admin_password,'email_code':code})
        self.assertEqual(changed.status_code,302)
        updated=self.layer.accounts.get_user(owner['id'])
        self.assertEqual(updated['email'],'owner@synthora.in')
        self.assertEqual(updated['pending_email'],'new-owner@synthora.in')
        verify=self.layer.accounts.create_email_code(owner['id'],'verify')
        self.assertTrue(self.layer.accounts.consume_email_code(owner['id'],'verify',verify))
        self.assertEqual(self.layer.accounts.verify_email_with_token(owner['id'])['email'],'new-owner@synthora.in')

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

    def test_youtube_section_is_the_download_account_not_a_per_user_action(self):
        owner = self.layer.accounts.get_user_by_username("owner")
        detail = self.client.get(f"/admin/users/{owner['id']}/")
        self.assertEqual(detail.status_code, 200)
        self.assertNotIn(b"youtube/signin", detail.data)
        self.assertNotIn(b"Sign in automatically", detail.data)

        page = self.client.get("/admin/youtube/")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"YouTube downloads", page.data)
        self.assertIn(b"not a per-user link", page.data)

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
        self.assert_no_token_in_any_email()
        # The administrator can send the code but cannot spend it: only the person
        # with the mailbox types it, on the account door, without signing in.
        confirmed = self.client.post(
            "/verify",
            data={"_csrf": self.ss_csrf(), "username": "verify-me",
                  "code": self.newest_outbox_code("verify")},
        )
        self.assertEqual(confirmed.status_code, 200, confirmed.get_data(as_text=True)[:400])
        self.assertTrue(self.layer.accounts.get_user(user["id"])["email_verified"])

        token = self.csrf()
        self.client.post("/admin/logout/", data={"_csrf": token})
        self.login("verify-me", "Temp!Passw0rd9")
        self.assertEqual(self.client.get("/admin/").status_code, 302)
        self.assertEqual(self.client.get("/admin/account/").status_code, 200)

    def test_invitation_sets_password_and_activates_account(self):
        self.create_admin("invited", "invited@example.com", invite=True)
        invited = self.layer.accounts.get_user_by_username("invited")
        self.assertEqual(invited["status"], "invited")

        token = self.newest_outbox_token("invite")
        # The link a stranger receives must not name the administrator panel.
        joined = "\n".join(self.outbox_bodies())
        self.assertIn(f"/invite/{token}", joined)
        self.assertNotIn("/admin/invite", joined)
        page = self.client.get(f"/invite/{token}")
        self.assertEqual(page.status_code, 200)
        response = self.client.post(
            f"/invite/{token}",
            data={
                # The invitation is accepted on the public door, which keeps its own
                # page token; the administrator who sent it never appears here.
                "_csrf": self.ss_csrf(f"/invite/{token}"),
                "password": "Activated!Pass9",
                "confirm_password": "Activated!Pass9",
            },
        )
        # The door answers with a confirmation rather than a redirect, because the
        # person following an invitation may never have seen this deployment before
        # and has nothing to be sent "back to".
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True)[:400])
        self.assertIn("Account activated", response.get_data(as_text=True))
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
        self.assert_no_token_in_any_email()
        form = self.client.get("/admin/reset-password/")
        self.assertEqual(form.status_code, 200)
        reset = self.client.post(
            "/admin/reset-password/",
            data={
                "_csrf": self.csrf(),
                "identifier": "owner",
                "code": self.newest_outbox_code("reset"),
                "password": "Reset!Passw0rd2",
                "confirm_password": "Reset!Passw0rd2",
            },
        )
        self.assertEqual(reset.status_code, 302, reset.get_data(as_text=True)[:400])
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
        confirm = self.client.get(f"/admin/magic/{magic}")
        self.assertEqual(confirm.status_code, 200)
        signed_in = self.client.post(f"/admin/magic/{magic}", data={"_csrf": self.csrf()})
        self.assertEqual(signed_in.status_code, 302)
        self.assertEqual(self.client.get("/admin/").status_code, 200)

    def test_expired_or_forged_credentials_are_rejected(self):
        self.assertEqual(self.client.get("/admin/magic/not-a-real-token").status_code, 410)
        self.assertEqual(self.client.get("/invite/not-a-real-token").status_code, 410)
        # The routes that used to take a token out of the URL are gone, not merely
        # unreachable: a stale email link must not resolve to anything at all.
        self.assertEqual(self.client.get("/admin/verify-email/not-a-real-token").status_code, 404)
        self.assertEqual(self.client.get("/admin/reset-password/not-a-real-token").status_code, 404)
        # A forged code, typed at the door that replaced those links.
        forged = self.client.post(
            "/admin/reset-password/",
            data={"_csrf": self.csrf(), "identifier": "owner", "code": "000000",
                  "password": "Reset!Passw0rd2", "confirm_password": "Reset!Passw0rd2"},
        )
        self.assertEqual(forged.status_code, 410)

    def test_email_code_cannot_survive_an_address_change(self):
        owner=self.layer.accounts.get_user_by_username('owner')
        reset=self.layer.accounts.create_email_code(owner['id'],'reset')
        proof=self.layer.accounts.create_email_code(owner['id'],'reauth')
        self.layer.accounts.update_own_profile(owner,email='new@synthora.in',current_password=self.admin_password,email_code=proof)
        verify=self.layer.accounts.create_email_code(owner['id'],'verify')
        self.assertTrue(self.layer.accounts.consume_email_code(owner['id'],'verify',verify))
        self.layer.accounts.verify_email_with_token(owner['id'])
        self.assertFalse(self.layer.accounts.consume_email_code(owner['id'],'reset',reset))


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
        # (failures, was_linked): the second half is what stops a caller from
        # promising a removal for a service this account never connected.
        self.assertEqual(
            self.layer.accounts.clear_provider_credentials(owner, owner["id"], "youtube"),
            ([], True))
        self.assertEqual(self.layer.accounts.provider_status(owner["id"]), [])
        self.assertEqual(
            self.layer.accounts.clear_provider_credentials(owner, owner["id"], "youtube"),
            ([], False))

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


class AdminControlsTests(MultiUserTestBase):
    """Controls the panel promises must exist and must actually reach the account."""

    def setUp(self):
        super().setUp()
        self.login()

    def _make_user(self, username: str = "paneluser"):
        response = self.client.post(
            "/admin/users/new/",
            data={
                "_csrf": self.csrf(),
                "username": username,
                "email": f"{username}@example.com",
                "role": "user",
                "password": "Panel!Passw0rd9",
            },
        )
        self.assertEqual(response.status_code, 302, response.get_data(as_text=True)[:400])
        return self.layer.accounts.get_user_by_username(username)

    def test_a_deleted_account_is_reachable_and_restorable(self):
        # The restore endpoint existed with zero UI, so a soft delete was a
        # one-way door: the row survived and no page could reach it.
        user = self._make_user()
        self.client.post(
            f"/admin/users/{user['id']}/delete/", data={"_csrf": self.csrf()}
        )
        listing = self.client.get("/admin/users/?status=deleted")
        self.assertEqual(listing.status_code, 200)
        self.assertIn(b"@paneluser", listing.data)
        self.assertIn(f"/admin/users/{user['id']}/restore/".encode(), listing.data)

        detail = self.client.get(f"/admin/users/{user['id']}/")
        self.assertEqual(detail.status_code, 200)
        self.assertIn(b"This account is deleted", detail.data)
        self.assertIn(b"Restore account", detail.data)

        response = self.client.post(
            f"/admin/users/{user['id']}/restore/", data={"_csrf": self.csrf()}
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn(f"/admin/users/{user['id']}/", response.headers["Location"])
        restored = self.layer.accounts.get_user(user["id"])
        self.assertEqual(restored["status"], "disabled")

    def test_the_forced_password_change_flag_is_visible_and_clearable(self):
        user = self._make_user()
        self.assertFalse(user["must_change_password"])
        detail = self.client.get(f"/admin/users/{user['id']}/")
        self.assertIn(b"Require a password change", detail.data)

        self.client.post(
            f"/admin/users/{user['id']}/password-change/",
            data={"_csrf": self.csrf(), "state": "require"},
        )
        self.assertTrue(self.layer.accounts.get_user(user["id"])["must_change_password"])
        detail = self.client.get(f"/admin/users/{user['id']}/")
        self.assertIn(b"This account must choose a new password", detail.data)

        self.client.post(
            f"/admin/users/{user['id']}/password-change/",
            data={"_csrf": self.csrf(), "state": "clear"},
        )
        self.assertFalse(self.layer.accounts.get_user(user["id"])["must_change_password"])

    def test_the_override_buttons_ask_before_they_fire(self):
        user = self._make_user()
        secret_enc = self.layer.secret_box.encrypt_json({"secret": "JBSWY3DPEHPK3PXP"})
        self.layer.db.execute(
            "UPDATE users SET totp_secret_enc = ? WHERE id = ?", (secret_enc, user["id"])
        )
        page = self.client.get(f"/admin/users/{user['id']}/").data
        # Each of these changes the account's ability to sign in, so each is a
        # confirmation, not a routine click.
        self.assertIn(b'data-confirm="Reset the two-factor secret', page)
        self.assertIn(b'data-confirm="Mark paneluser@example.com as verified', page)
        self.assertIn(b'data-confirm="Sign out every device on @paneluser', page)
        self.assertIn(b'data-confirm="Delete paneluser', page)

    def test_the_admin_can_turn_on_and_off_their_own_two_factor(self):
        # The panel an owner lives in used to say enrolment "arrives in Phase 6"
        # while the door strangers use could already do it.
        begin = self.client.post(
            "/admin/account/totp/begin/",
            data={"_csrf": self.csrf(), "current_password": self.admin_password},
        )
        self.assertEqual(begin.status_code, 302, begin.get_data(as_text=True)[:300])
        owner = self.layer.accounts.get_user_by_username("owner")
        with self.client.session_transaction() as state:
            session_token=state["mu_sid"]
        pending = self.layer.accounts.pending_totp_enrollment(owner, session_token=session_token)
        self.assertTrue(pending, "expected a pending enrolment")
        secret = re.search(r"secret=([A-Z2-7]+)", pending["otpauth_uri"]).group(1)

        qr = self.client.get("/admin/account/totp/qr/")
        self.assertEqual(qr.status_code, 200)
        self.assertEqual(qr.headers["Cache-Control"], "no-store")

        code = totp_at(secret, int(time.time()) // 30)
        confirm = self.client.post(
            "/admin/account/totp/confirm/", data={"_csrf": self.csrf(), "code": code}
        )
        self.assertEqual(confirm.status_code, 302)
        self.assertTrue(self.layer.accounts.get_user_by_username("owner")["has_totp"])
        self.assertIn(b"Remove authenticator", self.client.get("/admin/account/").data)

        recovery=self.layer.accounts.create_email_code(owner["id"],"reauth")
        off = self.client.post(
            "/admin/account/totp/disable/",
            data={
                "_csrf": self.csrf(),
                "current_password": self.admin_password,
                "code": recovery,
            },
        )
        self.assertEqual(off.status_code, 302)
        self.assertFalse(self.layer.accounts.get_user_by_username("owner")["has_totp"])

    def test_changing_your_own_password_does_not_sign_you_out(self):
        # The panel's own password form used to omit keep_sid_hash, so the page
        # flashed "Password changed." and then bounced to the sign-in screen.
        response = self.client.post(
            "/admin/account/",
            data={
                "_csrf": self.csrf(),
                "current_password": self.admin_password,
                "new_password": "NewOwner!Pass9",
                "confirm_password": "NewOwner!Pass9",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.get("/admin/").status_code, 200)


if __name__ == "__main__":
    unittest.main()
