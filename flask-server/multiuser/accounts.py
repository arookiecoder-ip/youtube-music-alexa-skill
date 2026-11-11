"""Account, session, credential and audit domain services.

This module has no Flask imports so it can be unit-tested and, later, reused by
a worker or admin CLI without booting the whole application.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
import uuid
from typing import Any
from urllib.parse import quote

from . import devicename
from .config import MultiUserConfig
from .crypto import SecretBox, SecretBoxError
from .db import Database
from .security import (
    generate_password,
    generate_totp_secret,
    hash_password,
    new_numeric_code,
    new_token,
    safe_equal,
    sha256_hex,
    totp_verify,
    totp_counter,
    validate_password,
    verify_password,
)

USERNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,31}$")
RESERVED_USERNAMES = {"anonymous", "system", "deleted", "root", "administrator"}
ROLES = ("owner", "admin", "user")
STATUSES = ("active", "disabled", "invited")
PROVIDERS = ("youtube", "amazon")
# Six digits is what an authenticator app offers to autofill and what a person can
# read off a phone screen in one glance.  The guess budget comes from
# ``MULTIUSER_EMAIL_CODE_ATTEMPTS``, not from more digits.
CODE_DIGITS = 6
# The one row in ``system_settings`` today: the administrator's switch for the
# public sign-up door.
SIGNUP_SWITCH_KEY = "signup_enabled"

logger = logging.getLogger(__name__)
_DUMMY_PASSWORD_HASH = hash_password("not-a-user-credential")


class AccountError(ValueError):
    """A validation or permission error safe to show to an administrator."""


def _now() -> float:
    return time.time()


def _normalize_username(username: str) -> str:
    return (username or "").strip()


def _normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def _status_transition_problem(current: str, requested: str) -> str | None:
    """Why this status change is not one anybody should be allowed to make.

    ``invited`` is a starting state meaning "no password accepted yet", so it is
    only ever set at creation.  Putting an already-activated account back to it used
    to lock the person out -- ``authenticate`` refuses everything that is not
    ``active`` -- while leaving ``email_verified`` at 1, which is a row claiming to
    be both verified and never activated.  Blocking an account is what ``disabled``
    is for, and that one reverses.
    """
    if requested == "invited" and current != "invited":
        return (
            "An account that has already been activated cannot be set back to invited. "
            "Disable it to block sign-in, or delete it."
        )
    return None


def _public_user(row: Any) -> dict:
    if row is None:
        return {}
    user = dict(row)
    user.pop("password_hash", None)
    user.pop("totp_secret_enc", None)
    user.pop("totp_last_step", None)
    user.pop("totp_pending_enc", None)
    user.pop("failed_login_count", None)
    user.pop("locked_until", None)
    user.pop("last_email_at", None)
    for private in ("totp_pending_session", "totp_failed_count", "totp_locked_until"):
        user.pop(private, None)
    user["has_totp"] = bool(dict(row).get("totp_secret_enc"))
    # An enrollment is only a live second factor once its code has been entered;
    # until then it is a secret sitting in a drawer, and must not read as "on".
    # Expired enrollments are swept on read, so this flag is never stale.
    user["totp_pending"] = bool(dict(row).get("totp_pending_enc"))
    user["email_verified"] = bool(dict(row).get("email_verified"))
    return user


class AccountService:
    def __init__(self, db: Database, config: MultiUserConfig, secret_box: SecretBox):
        self.db = db
        self.config = config
        self.secret_box = secret_box
        self._purge_hook = None
        self._artifact_hook = None

    # ------------------------------------------------------------------ setup
    def bootstrap(self) -> dict:
        """Create the first owner once. Never overwrites existing accounts."""
        self.db.initialize()
        existing = self.db.scalar("SELECT COUNT(*) FROM users") or 0
        if existing:
            self.legacy_owner_id()
            return {"created": False}
        password = self.config.admin_password
        generated = False
        password_file = ""
        if not password:
            password = generate_password()
            generated = True
            password_file = os.path.join(
                os.path.dirname(os.path.abspath(self.config.db_path)),
                "multiuser_admin_password.txt",
            )
            try:
                with open(password_file, "w", encoding="utf-8") as handle:
                    handle.write(password + "\n")
                os.chmod(password_file, 0o600)
            except OSError:
                password_file = ""
        else:
            problem = validate_password(password, self.config.admin_username)
            if problem:
                raise AccountError(f"Bootstrap admin password rejected: {problem}")
        user_id = str(uuid.uuid4())
        now = _now()
        username = self.config.admin_username or "admin"
        with self.db.connect() as conn:
            bootstrap_email = _normalize_email(self.config.admin_email)
            conn.execute(
                "INSERT INTO users (id, username, email, password_hash, role, status, "
                "must_change_password, totp_secret_enc, failed_login_count, locked_until, created_at, "
                "updated_at, last_login_at, created_by, deleted_at, email_verified, email_verified_at, "
                "last_email_at) "
                "VALUES (?, ?, ?, ?, 'owner', 'active', 1, '', 0, 0, ?, ?, 0, 'system', 0, ?, ?, 0)",
                (
                    user_id,
                    username,
                    bootstrap_email,
                    hash_password(password),
                    now,
                    now,
                    1 if bootstrap_email else 0,
                    now if bootstrap_email else 0,
                ),
            )
        self.legacy_owner_id()
        self.db.audit(
            None, "system", "system_bootstrap", user_id,
            json.dumps({"role": "owner", "generated_password_file": password_file}),
        )
        return {
            "created": True,
            "user_id": user_id,
            "username": username,
            "generated_password": generated,
            "password_file": password_file,
        }

    # ------------------------------------------------------------------- read
    def get_user(self, user_id: str, include_deleted: bool = False) -> dict:
        if include_deleted:
            return _public_user(
                self.db.fetchone("SELECT * FROM users WHERE id = ?", (user_id or "",))
            )
        return _public_user(
            self.db.fetchone("SELECT * FROM users WHERE id = ? AND status <> 'deleted'", (user_id or "",))
        )

    def get_user_by_username(self, username: str) -> dict:
        return _public_user(
            self.db.fetchone("SELECT * FROM users WHERE username = ? AND status <> 'deleted'", (username or "",))
        )

    def counts(self) -> dict[str, int]:
        return self.db.counts()

    def list_users(
        self,
        query: str = "",
        status: str = "",
        role: str = "",
        page: int = 1,
        per_page: int = 25,
    ) -> tuple[list[dict], int]:
        page = max(1, int(page or 1))
        per_page = min(100, max(5, int(per_page or 25)))
        # ``deleted`` is a status filter like any other, but it is the one that
        # shows soft-deleted rows instead of hiding them: that is the only place
        # an owner can find an account to restore.
        if status == "deleted":
            clauses = ["status = 'deleted'"]
        else:
            clauses = ["status <> 'deleted'"]
        params: list[Any] = []
        if query:
            like = f"%{query.strip()}%"
            clauses.append("(username LIKE ? OR email LIKE ?)")
            params.extend([like, like])
        if status in STATUSES:
            clauses.append("status = ?")
            params.append(status)
        if role in ROLES:
            clauses.append("role = ?")
            params.append(role)
        where = " AND ".join(clauses)
        total = int(self.db.scalar(f"SELECT COUNT(*) FROM users WHERE {where}", tuple(params)) or 0)
        rows = self.db.fetchall(
            f"SELECT * FROM users WHERE {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            tuple(params + [per_page, (page - 1) * per_page]),
        )
        users = [_public_user(row) for row in rows]
        self._attach_provider_status(users)
        return users, total

    def deleted_users(self, page: int = 1, per_page: int = 25) -> tuple[list[dict], int]:
        page = max(1, int(page or 1))
        per_page = min(100, max(5, int(per_page or 25)))
        total = int(self.db.scalar("SELECT COUNT(*) FROM users WHERE status = 'deleted'") or 0)
        rows = self.db.fetchall(
            "SELECT * FROM users WHERE status = 'deleted' ORDER BY deleted_at DESC LIMIT ? OFFSET ?",
            (per_page, (page - 1) * per_page),
        )
        return [_public_user(row) for row in rows], total

    def _attach_provider_status(self, users: list[dict]) -> None:
        if not users:
            return
        ids = [user["id"] for user in users]
        placeholders = ",".join("?" for _ in ids)
        rows = self.db.fetchall(
            "SELECT user_id, provider, status, last_error, updated_at, last_validated_at "
            f"FROM user_provider_credentials WHERE user_id IN ({placeholders})",
            tuple(ids),
        )
        by_user: dict[str, dict[str, dict]] = {}
        for row in rows:
            by_user.setdefault(row["user_id"], {})[row["provider"]] = dict(row)
        for user in users:
            user["providers"] = by_user.get(user["id"], {})

    # --------------------------------------------------------------- validate
    @staticmethod
    def validate_username(username: str) -> str:
        username = _normalize_username(username)
        if not username:
            raise AccountError("Username is required.")
        if not USERNAME_RE.match(username):
            raise AccountError(
                "Username must be 3-32 characters: letters, numbers, dot, dash or underscore, "
                "starting with a letter or number."
            )
        if username.lower() in RESERVED_USERNAMES:
            raise AccountError("That username is reserved.")
        return username

    @staticmethod
    def validate_email(email: str) -> str:
        email = _normalize_email(email)
        if not email:
            return ""
        if len(email) > 254 or not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
            raise AccountError("Enter a valid email address, or leave it blank.")
        return email

    def _ensure_unique(self, username: str, email: str, exclude_id: str = "") -> None:
        row = self.db.fetchone(
            "SELECT id FROM users WHERE username = ? COLLATE NOCASE AND id <> ?",
            (username, exclude_id),
        )
        if row:
            raise AccountError("That username is already in use.")
        if email:
            row = self.db.fetchone(
                "SELECT id FROM users WHERE email = ? COLLATE NOCASE AND email <> '' AND id <> ?",
                (email, exclude_id),
            )
            if row:
                raise AccountError("That email address is already in use.")

    # --------------------------------------------------------------- permissions
    @staticmethod
    def can_view_admin(actor: dict | None) -> bool:
        return bool(actor and actor.get("role") in {"owner", "admin"} and actor.get("status") == "active")

    @staticmethod
    def can_manage_user(actor: dict | None, target: dict | None) -> bool:
        if not actor or actor.get("status") != "active":
            return False
        if actor.get("role") == "owner":
            return True
        if actor.get("role") == "admin":
            return bool(target and target.get("role") == "user")
        return False

    @staticmethod
    def can_assign_role(actor: dict | None, role: str) -> bool:
        if not actor:
            return False
        if actor.get("role") == "owner":
            return role in ROLES
        if actor.get("role") == "admin":
            return role == "user"
        return False

    @staticmethod
    def status_options(status: str) -> list:
        """The statuses the edit form may offer for an account that is *status*.

        The service rule in ``_status_transition_problem`` is the authority; this
        only keeps the dropdown from presenting a choice that would be refused.
        """
        return {
            "invited": ["invited", "active"],
            "active": ["active", "disabled"],
            "disabled": ["disabled", "active"],
        }.get(status or "", [])
    def _active_owner_count(self, excluding_user_id: str = "") -> int:
        return int(
            self.db.scalar(
                "SELECT COUNT(*) FROM users WHERE role = 'owner' AND status = 'active' AND id <> ?",
                (excluding_user_id or "",),
            )
            or 0
        )

    def _guard_last_owner(self, target: dict, changes: dict) -> None:
        if target.get("role") != "owner" or target.get("status") != "active":
            return
        loses_owner = changes.get("role", target.get("role")) != "owner"
        loses_active = changes.get("status", target.get("status")) != "active"
        if (loses_owner or loses_active) and self._active_owner_count(excluding_user_id=target["id"]) == 0:
            raise AccountError("At least one active owner account must remain.")

    # ------------------------------------------------------------------ create
    def create_user(
        self,
        actor: dict | None,
        username: str,
        password: str,
        email: str = "",
        role: str = "user",
        must_change_password: bool = False,
        status: str = "active",
        email_verified: bool = False,
        ip: str = "",
        user_agent: str = "",
    ) -> dict:
        if not self.can_view_admin(actor):
            raise AccountError("You do not have permission to create users.")
        username = self.validate_username(username)
        email = self.validate_email(email)
        role = (role or "user").strip().lower()
        status = (status or "active").strip().lower()
        if role not in ROLES:
            raise AccountError("Invalid role.")
        if status not in {"active", "invited"}:
            raise AccountError("Invalid initial status.")
        if status == "invited" and not email:
            raise AccountError("An email address is required to send an invitation.")
        if not self.can_assign_role(actor, role):
            raise AccountError("You cannot assign that role.")
        self._ensure_unique(username, email)
        problem = validate_password(password, username)
        if problem:
            raise AccountError(problem)
        user_id = self._insert_user(
            username=username,
            password=password,
            email=email,
            role=role,
            status=status,
            must_change_password=must_change_password,
            email_verified=email_verified,
            created_by=actor.get("id") if actor else None,
        )
        self.audit(
            actor, "user_created", user_id,
            {"username": username, "role": role, "must_change_password": must_change_password},
            ip, user_agent,
        )
        return self.get_user(user_id)

    def _insert_user(
        self,
        *,
        username: str,
        password: str,
        email: str = "",
        role: str = "user",
        status: str = "active",
        must_change_password: bool = True,
        email_verified: bool = False,
        created_by: str | None = None,
    ) -> str:
        """The only place a ``users`` row is written, so the columns an account
        may not choose for itself (role, verification) stay chosen here."""
        user_id = str(uuid.uuid4())
        now = _now()
        verified = bool(email_verified and email)
        with self.db.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            if created_by == 'self':
                count = conn.execute("SELECT COUNT(*) FROM users WHERE created_by='self' AND created_at>=?", (now-86400,)).fetchone()[0]
                setting = conn.execute("SELECT setting_value FROM system_settings WHERE setting_key=?", (SIGNUP_SWITCH_KEY,)).fetchone()
                if not self.config.self_signup_enabled or (setting and setting[0] != '1') or count >= self.config.max_self_signups_per_day:
                    raise AccountError('Sign-up is not available right now.')
            conn.execute(
                "INSERT INTO users (id, username, email, password_hash, role, status, "
                "must_change_password, totp_secret_enc, failed_login_count, locked_until, created_at, "
                "updated_at, last_login_at, created_by, deleted_at, email_verified, email_verified_at, "
                "last_email_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, '', 0, 0, ?, ?, 0, ?, 0, ?, ?, 0)",
                (
                    user_id,
                    username,
                    email,
                    hash_password(password),
                    role,
                    status,
                    1 if must_change_password else 0,
                    now,
                    now,
                    created_by,
                    1 if verified else 0,
                    now if verified else 0,
                ),
            )
        return user_id

    # ------------------------------------------------------ self-registration
    # ------------------------------------------------------ self-registration
    def system_setting(self, key: str, default: str = "") -> str:
        row = self.db.fetchone(
            "SELECT setting_value FROM system_settings WHERE setting_key = ?", (key,)
        )
        return row["setting_value"] if row else default

    def system_setting_row(self, key: str) -> dict:
        """Value plus who set it and when, for the page that has to name its source."""
        row = self.db.fetchone(
            "SELECT s.setting_value, s.updated_at, s.updated_by, u.username "
            "FROM system_settings s LEFT JOIN users u ON u.id = s.updated_by "
            "WHERE s.setting_key = ?",
            (key,),
        )
        return dict(row) if row else {}

    def set_system_setting(
        self,
        actor: dict | None,
        key: str,
        value: str,
        ip: str = "",
        user_agent: str = "",
    ) -> None:
        before = self.system_setting(key)
        with self.db.connect() as conn:
            conn.execute(
                "INSERT INTO system_settings (setting_key, setting_value, updated_at, updated_by) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(setting_key) DO UPDATE SET "
                "setting_value = excluded.setting_value, updated_at = excluded.updated_at, "
                "updated_by = excluded.updated_by",
                (key[:80], (value or "")[:120], _now(), (actor or {}).get("id") or ""),
            )
        self.audit(
            actor, "system_setting_changed", None,
            {"key": key[:80], "before": before[:120], "after": (value or "")[:120]},
            ip, user_agent,
        )

    def signup_switch_on(self) -> bool:
        """The administrator's half of the sign-up policy.  No row means open."""
        return self.system_setting(SIGNUP_SWITCH_KEY, "1") == "1"

    def set_signup_switch(
        self, actor: dict | None, enabled: bool, ip: str = "", user_agent: str = ""
    ) -> None:
        if not self.config.self_signup_enabled:
            # The environment is the ceiling: a hijacked panel session must not be
            # able to open a door the deployment shut on purpose.
            raise AccountError(
                "MULTIUSER_SELF_SIGNUP_ENABLED is 0, so this deployment has no public "
                "sign-up to switch on. Change the environment, not the panel."
            )
        self.set_system_setting(
            actor, SIGNUP_SWITCH_KEY, "1" if enabled else "0", ip, user_agent
        )

    def signup_policy(self) -> dict:
        """Whether open sign-up may create an account, and why not if it may not.

        Three separate reasons, because the page owes an applicant the truth: a
        exhausted daily cap invites them back tomorrow, an administrator's closure
        sends them to support, and a deployment with no sign-up at all is neither.
        """
        if not self.config.self_signup_enabled or self.config.max_self_signups_per_day <= 0:
            return {"open": False, "reason": "environment", "remaining": 0}
        if not self.signup_switch_on():
            return {"open": False, "reason": "administrator", "remaining": 0}
        limit = int(self.config.max_self_signups_per_day)
        count = self.recent_self_signup_count()
        if count >= limit:
            return {
                "open": False, "reason": "daily_cap", "remaining": 0,
                "count": count, "limit": limit,
            }
        return {
            "open": True, "reason": "", "remaining": limit - count,
            "count": count, "limit": limit,
        }

    def recent_self_signup_count(self, seconds: int = 86400) -> int:
        """Accounts created by open sign-up in the window.

        The in-process rate limiter forgets everything on restart, so this count
        is the durable half of the abuse control."""
        return int(
            self.db.scalar(
                "SELECT COUNT(*) FROM users WHERE created_by = 'self' AND created_at >= ?",
                (_now() - max(1, int(seconds)),),
            )
            or 0
        )

    def registration_open(self) -> bool:
        return bool(self.signup_policy()["open"])

    def register_self_service(
        self,
        *,
        username: str,
        password: str,
        email: str,
        ip: str = "",
        user_agent: str = "",
    ) -> dict:
        """Create an account with no administrator involved.

        Privilege is not on offer: role, status and verification are fixed by the
        caller, and the account cannot sign in until the address on it is
        confirmed, so an abandoned registration is harmless.
        """
        if not self.registration_open():
            raise AccountError("Sign-up is not available right now.")
        username = self.validate_username(username)
        email = self.validate_email(email)
        if not email:
            raise AccountError("An email address is required, so you can reset your password later.")
        problem = validate_password(password, username)
        if problem:
            raise AccountError(problem)
        self._ensure_unique(username, email)
        user_id = self._insert_user(
            username=username,
            password=password,
            email=email,
            role="user",
            status="active",
            must_change_password=False,
            email_verified=False,
            created_by="self",
        )
        self.db.audit(
            None, username[:120], "signup", user_id,
            json.dumps({"email_domain": email.rsplit("@", 1)[-1]}), ip, user_agent,
        )
        return self.get_user(user_id)

    def verification_pending(self, user: dict | None) -> bool:
        if not user:
            return False
        return bool(user.get("pending_email")) or bool(user.get("email")) and not user.get("email_verified") and (
            self._must_verify(user.get("created_by"))
        )

    def _must_verify(self, created_by: Any) -> bool:
        """Self-registered accounts always confirm their address.

        An operator may turn the general requirement off, but that switch cannot
        silently hand an unverified self-signup the right to play."""
        return bool(self.config.require_email_verification or created_by == "self")

    # ------------------------------------------------------------------ update
    def update_user(
        self,
        actor: dict | None,
        user_id: str,
        changes: dict,
        ip: str = "",
        user_agent: str = "",
    ) -> dict:
        target = self.get_user(user_id)
        if not target:
            raise AccountError("User not found.")
        if not self.can_manage_user(actor, target) and not (
            actor and actor.get("id") == target.get("id")
        ):
            raise AccountError("You do not have permission to edit this user.")

        allowed = {"username", "email", "role", "status"}
        changes = {key: value for key, value in (changes or {}).items() if key in allowed}
        if not changes:
            raise AccountError("Nothing to update.")

        if "username" in changes:
            changes["username"] = self.validate_username(changes["username"])
        if "email" in changes:
            changes["email"] = self.validate_email(changes["email"])
            if actor and actor.get("id") == user_id and changes["email"] != target.get("email"):
                raise AccountError("Use your account security page to change your email address.")
        if "role" in changes:
            changes["role"] = (changes.get("role") or "").strip().lower()
            if changes["role"] not in ROLES:
                raise AccountError("Invalid role.")
            if actor and actor.get("id") == user_id:
                raise AccountError("You cannot change your own role.")
            if not self.can_assign_role(actor, changes["role"]):
                raise AccountError("You cannot assign that role.")
        if "status" in changes:
            changes["status"] = (changes.get("status") or "").strip().lower()
            if changes["status"] not in STATUSES:
                raise AccountError("Invalid status.")
            if actor and actor.get("id") == user_id:
                raise AccountError("You cannot change your own status.")
            problem = _status_transition_problem(target.get("status") or "", changes["status"])
            if problem:
                raise AccountError(problem)
            if changes["status"] == "invited" and not (
                changes.get("email") or target.get("email")
            ):
                raise AccountError("An email address is required to invite an account.")

        self._guard_last_owner(target, changes)
        self._ensure_unique(
            changes.get("username", target["username"]),
            changes.get("email", target["email"]),
            exclude_id=user_id,
        )
        # Changing the address invalidates the old proof of control. The user
        # must verify the new address before email-based sign-in/reset works.
        if "email" in changes and changes["email"] != (target.get("email") or ""):
            changes["email_verified"] = 0
            changes["email_verified_at"] = 0

        assignments = ", ".join(f"{key} = ?" for key in changes)
        params = list(changes.values()) + [_now(), user_id]
        with self.db.connect() as conn:
            conn.execute(f"UPDATE users SET {assignments}, updated_at = ? WHERE id = ?", tuple(params))
        self.audit(actor, "user_updated", user_id, {"fields": sorted(changes)}, ip, user_agent)
        return self.get_user(user_id)

    def update_own_profile(
        self,
        actor: dict | None,
        *,
        email: str,
        current_password: str = "",
        email_code: str = "",
        ip: str = "",
        user_agent: str = "",
    ) -> dict:
        """Change the email address on the account you are signed in to.

        A changed address requires the current password and starts unverified, so a
        hijacked session cannot silently take over password reset and magic-link
        sign-in for an address the attacker controls.  There is no display name to
        edit: the username is the one name an account has, and it is what the queue,
        the audit log and every other player already quote.
        """
        if not actor:
            raise AccountError("Sign in first.")
        target = self.get_user(actor.get("id", ""))
        if not target:
            raise AccountError("User not found.")
        email = self.validate_email(email)
        email_changed = email != (target.get("email") or "").strip().lower()
        if not email_changed:
            return target
        row = self.db.fetchone(
            "SELECT password_hash FROM users WHERE id = ? AND status = 'active'",
            (target["id"],),
        )
        if not row or not verify_password(row["password_hash"], current_password):
            raise AccountError("Enter your current password to change the email address.")
        self._ensure_unique(target["username"], email, exclude_id=target["id"])
        self.confirm_own_credentials(actor, current_password=current_password, email_code=email_code,
                                     require_email_code=bool(target.get("email")), ip=ip, user_agent=user_agent)
        now = _now()
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE users SET pending_email = ?, pending_email_at = ?, "
                "updated_at = ? WHERE id = ?",
                (email, now, now, target["id"]),
            )
            conn.execute("DELETE FROM user_email_tokens WHERE user_id = ?", (target["id"],))
        self.audit(
            actor, "profile_updated", target["id"], {"email_changed": True}, ip, user_agent
        )
        self.security_notice(target, "Email change requested", "A replacement recovery email was requested. Your current address stays active until the new address is confirmed. If this was not you, change your password and revoke other sessions.")
        return self.get_user(target["id"])

    def set_status(
        self,
        actor: dict | None,
        user_id: str,
        status: str,
        ip: str = "",
        user_agent: str = "",
    ) -> dict:
        status = (status or "").strip().lower()
        if status not in {"active", "disabled"}:
            raise AccountError("Status must be active or disabled.")
        target = self.get_user(user_id)
        if not target:
            raise AccountError("User not found.")
        if not self.can_manage_user(actor, target):
            raise AccountError("You do not have permission to change this user.")
        if actor.get("id") == user_id:
            raise AccountError("You cannot disable your own account.")
        self._guard_last_owner(target, {"status": status})
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE users SET status = ?, updated_at = ? WHERE id = ?",
                (status, _now(), user_id),
            )
            if status == "disabled":
                conn.execute(
                    "UPDATE user_sessions SET revoked_at = ? WHERE user_id = ? AND revoked_at = 0",
                    (_now(), user_id),
                )
        self.audit(actor, "user_status_changed", user_id, {"status": status}, ip, user_agent)
        return self.get_user(user_id)

    def set_must_change_password(
        self,
        actor: dict | None,
        user_id: str,
        required: bool,
        ip: str = "",
        user_agent: str = "",
    ) -> dict:
        """Arm or clear the forced-password-change flag.

        The flag is set when an account is created and cleared by the next
        password change, but an administrator also needs to clear it when the
        holder changed the password some other way (a reset, for instance), and to
        arm it again after handing over a temporary one."""
        target = self.get_user(user_id)
        if not target:
            raise AccountError("User not found.")
        if not self.can_manage_user(actor, target):
            raise AccountError("You do not have permission to change this user.")
        self.db.execute(
            "UPDATE users SET must_change_password = ?, updated_at = ? WHERE id = ?",
            (1 if required else 0, _now(), user_id),
        )
        self.audit(
            actor,
            "password_change_required" if required else "password_change_cleared",
            user_id,
            {},
            ip,
            user_agent,
        )
        return self.get_user(user_id)

    # ---------------------------------------------------------------- password
    def reset_password(
        self,
        actor: dict | None,
        user_id: str,
        password: str | None = None,
        ip: str = "",
        user_agent: str = "",
    ) -> tuple[dict, str, bool]:
        target = self.get_user(user_id)
        if not target:
            raise AccountError("User not found.")
        self_edit = bool(actor and actor.get("id") == user_id)
        if not self_edit and not self.can_manage_user(actor, target):
            raise AccountError("You do not have permission to reset this password.")
        generated = False
        if not password:
            password = generate_password()
            generated = True
        problem = validate_password(password, target["username"])
        if problem:
            raise AccountError(problem)
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE users SET password_hash = ?, must_change_password = 1, "
                "failed_login_count = 0, locked_until = 0, updated_at = ? WHERE id = ?",
                (hash_password(password), _now(), user_id),
            )
            conn.execute(
                "UPDATE user_sessions SET revoked_at = ? WHERE user_id = ? AND revoked_at = 0",
                (_now(), user_id),
            )
            conn.execute("DELETE FROM user_email_tokens WHERE user_id = ?", (user_id,))
        self.audit(actor, "password_reset", user_id, {"self": self_edit, "generated": generated}, ip, user_agent)
        return self.get_user(user_id), password, generated

    def change_own_password(
        self,
        actor: dict,
        current_password: str,
        new_password: str,
        ip: str = "",
        user_agent: str = "",
        *,
        keep_sid_hash: str = "",
        revoke_other_sessions: bool = True,
    ) -> None:
        row = self.db.fetchone("SELECT * FROM users WHERE id = ? AND status = 'active'", (actor.get("id", ""),))
        if not row or not verify_password(row["password_hash"], current_password):
            raise AccountError("Current password is incorrect.")
        problem = validate_password(new_password, row["username"])
        if problem:
            raise AccountError(problem)
        self._store_own_password(row, new_password, keep_sid_hash, revoke_other_sessions)
        self.audit(actor, "password_changed", row["id"], {"self": True}, ip, user_agent)

    def reset_own_password(
        self,
        actor: dict,
        *,
        email_code: str,
        new_password: str,
        totp_code: str = "",
        ip: str = "",
        user_agent: str = "",
        keep_sid_hash: str = "",
        revoke_other_sessions: bool = True,
    ) -> None:
        """Set a new password for the signed-in account without the old one.

        For the person already inside the panel who has lost the password: the
        mailbox answers, and the authenticator too when it is armed. The new password
        is checked for strength before a live code is spent on it."""
        row = self._active_row(actor)
        problem = validate_password(new_password, row["username"])
        if problem:
            raise AccountError(problem)
        # The authenticator is checked first so a mistyped one costs the person
        # nothing: the emailed code stays live, and a resend is a minute away.
        if row["totp_secret_enc"] and not self.verify_totp_code(row["id"], totp_code):
            self.db.audit(
                row["id"], row["username"], "password_reset_failed", row["id"],
                json.dumps({"reason": "bad_totp"}), ip, user_agent,
            )
            raise AccountError(
                "That authenticator code is not right, or it was already used. "
                "Try the next code your app shows."
            )
        if not email_code or not self.consume_email_code(row["id"], "reauth", email_code):
            self.db.audit(
                row["id"], row["username"], "password_reset_failed", row["id"],
                json.dumps({"reason": "bad_code"}), ip, user_agent,
            )
            raise AccountError(
                "That email code did not match, or it has expired. Ask for a fresh one."
            )
        self._store_own_password(row, new_password, keep_sid_hash, revoke_other_sessions)
        proof = "email+totp" if row["totp_secret_enc"] else "email"
        self.audit(
            actor, "password_reset", row["id"], {"self": True, "proof": proof},
            ip, user_agent,
        )

    def _store_own_password(
        self,
        row: dict,
        new_password: str,
        keep_sid_hash: str = "",
        revoke_other_sessions: bool = True,
    ) -> None:
        now = _now()
        keep = sha256_hex(keep_sid_hash) if keep_sid_hash else ""
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE users SET password_hash = ?, must_change_password = 0, updated_at = ? WHERE id = ?",
                (hash_password(new_password), now, row["id"]),
            )
            conn.execute("DELETE FROM user_email_tokens WHERE user_id = ?", (row["id"],))
            conn.execute("UPDATE user_sessions SET security_version=(SELECT security_version FROM users WHERE id=?) "
                         "WHERE user_id=? AND revoked_at=0" + (" AND sid_hash=?" if revoke_other_sessions else ""),
                         (row['id'], row['id'], keep) if revoke_other_sessions else (row['id'], row['id']))
            if revoke_other_sessions:
                # A password change is the moment to evict whoever else knew the old
                # one; without it a stolen password stays usable for 12 hours.
                conn.execute(
                    "UPDATE user_sessions SET revoked_at = ? "
                    "WHERE user_id = ? AND revoked_at = 0 AND sid_hash <> ?",
                    (now, row["id"], keep),
                )

        self.security_notice(dict(row), "Your password changed", "Your Music Box password was changed. If this was not you, reset it and revoke your other sessions.")

    def session_summaries(self, user_id: str, current_sid_hash: str = "") -> list[dict]:
        """This account's live sessions, with nothing usable about them included.

        A session id is the credential, so even its hash never leaves the server:
        the browser only gets a fingerprint to tell the rows apart."""
        summaries = []
        for entry in self.list_sessions(user_id):
            if entry["revoked_at"] or entry["expires_at"] <= _now():
                continue
            sid_hash = entry["sid_hash"]
            raw_agent = (entry["user_agent"] or "")[:120]
            summaries.append({
                "id": sid_hash[:8],
                "current": bool(current_sid_hash) and sid_hash == current_sid_hash,
                "created_at": entry["created_at"],
                "last_seen_at": entry["last_seen_at"],
                "expires_at": entry["expires_at"],
                "ip": entry["ip"],
                # A name to scan, plus the header it was read from. The label is
                # what a person compares against the devices they own; the raw
                # string is what they check it against when something is off.
                "device": devicename.describe_user_agent(raw_agent),
                "device_ua": raw_agent,
            })
        return summaries

    def _set_password_via_email(
        self,
        user_id: str,
        password: str,
        *,
        action: str,
        activate: bool = False,
        ip: str = "",
        user_agent: str = "",
        keep_sid_hash: str = "",
        revoke_other_sessions: bool = True,
    ) -> dict:
        target = self.get_user(user_id)
        if not target:
            raise AccountError("User not found.")
        problem = validate_password(password, target["username"])
        if problem:
            raise AccountError(problem)
        now = _now()
        with self.db.connect() as conn:
            if activate:
                conn.execute(
                    "UPDATE users SET password_hash = ?, status = 'active', must_change_password = 0, "
                    "email_verified = 1, email_verified_at = ?, failed_login_count = 0, locked_until = 0, "
                    "updated_at = ? WHERE id = ?",
                    (hash_password(password), now, now, user_id),
                )
            else:
                conn.execute(
                    "UPDATE users SET password_hash = ?, must_change_password = 0, email_verified = 1, "
                    "email_verified_at = ?, failed_login_count = 0, locked_until = 0, updated_at = ? "
                    "WHERE id = ?",
                    (hash_password(password), now, now, user_id),
                )
            if revoke_other_sessions:
                # A reset from a browser nobody else can see still means the old
                # password is spent, so everybody else goes -- unless the person
                # resetting from a signed-in session asks to keep their other
                # devices. `keep_sid_hash` spares the one asking; "" spares nobody.
                keep = sha256_hex(keep_sid_hash) if keep_sid_hash else ""
                conn.execute(
                    "UPDATE user_sessions SET revoked_at = ? "
                    "WHERE user_id = ? AND revoked_at = 0 AND sid_hash <> ?",
                    (now, user_id, keep),
                )
            conn.execute("DELETE FROM user_email_tokens WHERE user_id = ?", (user_id,))
            preserved = sha256_hex(keep_sid_hash) if keep_sid_hash else ''
            conn.execute("UPDATE user_sessions SET security_version=(SELECT security_version FROM users WHERE id=?) "
                         "WHERE user_id=? AND revoked_at=0" + (" AND sid_hash=?" if revoke_other_sessions else ""),
                         (user_id, user_id, preserved) if revoke_other_sessions else (user_id, user_id))
        self.db.audit(
            None, "email", action, user_id,
            json.dumps({"method": "email", "activated": activate}), ip, user_agent,
        )
        self.security_notice(target, "Your password changed", "Your Music Box password was set through email recovery. If this was not you, secure your recovery mailbox and contact your administrator.")
        return self.get_user(user_id)

    def reset_password_with_token(
        self,
        user_id: str,
        password: str,
        ip: str = "",
        user_agent: str = "",
        *,
        keep_sid_hash: str = "",
        revoke_other_sessions: bool = True,
    ) -> dict:
        return self._set_password_via_email(
            user_id,
            password,
            action="password_reset_completed",
            ip=ip,
            user_agent=user_agent,
            keep_sid_hash=keep_sid_hash,
            revoke_other_sessions=revoke_other_sessions,
        )

    def complete_invitation(
        self, user_id: str, password: str, ip: str = "", user_agent: str = ""
    ) -> dict:
        return self._set_password_via_email(
            user_id, password, action="invitation_accepted", activate=True, ip=ip, user_agent=user_agent
        )

    def reset_totp(self, actor: dict, user_id: str, ip: str = "", user_agent: str = "") -> None:
        target = self.get_user(user_id)
        if not target:
            raise AccountError("User not found.")
        if not self.can_manage_user(actor, target) and not (actor and actor.get("id") == user_id):
            raise AccountError("You do not have permission to reset 2FA.")
        self.db.execute(
            "UPDATE users SET totp_secret_enc = '', updated_at = ? WHERE id = ?",
            (_now(), user_id),
        )
        self.audit(actor, "totp_reset", user_id, {}, ip, user_agent)

    # --------------------------------------------------------------- 2FA (TOTP)
    def totp_is_enabled(self, user: dict | None) -> bool:
        return bool(user and user.get("has_totp"))

    def _active_row(self, actor: dict | None):
        if not actor or not actor.get("id"):
            raise AccountError("Sign in first.")
        row = self.db.fetchone(
            "SELECT * FROM users WHERE id = ? AND status = 'active'", (actor["id"],)
        )
        if not row:
            raise AccountError("This account is not active.")
        return row

    def _totp_secret(self, stored: str) -> str:
        if not stored:
            return ""
        try:
            payload = self.secret_box.decrypt_json(stored)
        except SecretBoxError:
            logger.exception("A stored two-factor secret could not be decrypted")
            return ""
        return str(payload.get("secret") or "") if isinstance(payload, dict) else ""

    def _sweep_totp_enrollment(self, user_id: str) -> None:
        """Drop an enrollment whose setup window closed.

        A half-finished enrollment is a secret in the database that grants
        nothing, so expiring it silently is always the safe direction."""
        deadline = _now() - max(1, int(self.config.totp_pending_minutes)) * 60
        if self.db.execute(
            "UPDATE users SET totp_pending_enc = '', totp_pending_at = 0, updated_at = ? "
            "WHERE id = ? AND totp_pending_enc <> '' AND totp_pending_at < ?",
            (_now(), user_id, deadline),
        ):
            self.db.audit(None, "system", "totp_enrollment_expired", user_id, "{}", "", "")

    def reauthenticate(
        self,
        actor: dict,
        *,
        password: str = "",
        email_code: str = "",
        ip: str = "",
        user_agent: str = "",
    ) -> str:
        """Prove the person at the keyboard owns this account: password or email code.

        Either one alone is enough here, which is what the panel's confirmation
        dialog offers. A stolen session has neither: it cannot read the mailbox and
        does not know a password the owner never typed into it."""
        row = self._active_row(actor)
        if email_code:
            if self.consume_email_code(row["id"], "reauth", email_code):
                return "email"
            reason = "bad_code"
        elif password:
            if verify_password(row["password_hash"], password):
                return "password"
            reason = "bad_password"
        else:
            raise AccountError("Enter your password or the code from your email.")
        self.db.audit(
            row["id"], row["username"], "totp_reauth_failed", row["id"],
            json.dumps({"reason": reason}), ip, user_agent,
        )
        raise AccountError(
            "That did not verify. Re-enter your password, or ask for a fresh code."
        )

    def confirm_own_credentials(
        self,
        actor: dict,
        *,
        current_password: str = "",
        email_code: str = "",
        purpose: str = "reauth",
        require_email_code: bool = True,
        ip: str = "",
        user_agent: str = "",
        audit_action: str = "security_change_failed",
    ) -> str:
        """Check the password, then spend an emailed code, and say which half failed.

        :meth:`reauthenticate` accepts one proof *or* the other, which is the wrong
        shape for the account page: changing an email address needs the code at the old
        address **and** the password, and handing both to that function checks only the
        code.  Here the password is always the first gate, so a wrong password never
        burns a live code -- the code stays usable and the person only has to fix the
        box they got wrong.
        """
        row = self._active_row(actor)
        if not verify_password(row["password_hash"], current_password):
            self.db.audit(
                row["id"], row["username"], audit_action, row["id"],
                json.dumps({"reason": "bad_password"}), ip, user_agent,
            )
            raise AccountError("Current password is incorrect.")
        if email_code:
            if not self.consume_email_code(row["id"], purpose, email_code):
                self.db.audit(
                    row["id"], row["username"], audit_action, row["id"],
                    json.dumps({"reason": "bad_code"}), ip, user_agent,
                )
                raise AccountError("That email code did not match, or it has expired.")
            return "password+email"
        if require_email_code:
            raise AccountError("Enter the code that was emailed to you.")
        return "password"

    def begin_totp_enrollment(
        self,
        actor: dict,
        *,
        current_password: str = "",
        email_code: str = "",
        issuer: str = "",
        ip: str = "",
        user_agent: str = "",
        verified: bool = False,
        allow_existing: bool = False,
        session_token: str = "",
    ) -> dict:
        """Hand out a fresh secret, held *pending* until a code proves the clock matches.

        The password is required because a hijacked session must not be able to arm
        a second factor it alone can satisfy -- that turns 2FA into a lockout tool
        against the real owner. An emailed code is accepted as an optional extra
        proof, never demanded, for the same reason it is optional when turning the
        factor off. ``verified`` says that proof was already made, by the
        re-authentication dialog; ``allow_existing`` is the "change authenticator"
        case, where the live secret keeps working until the new one is confirmed.
        """
        row = self._active_row(actor)
        if not row["email"] or not row["email_verified"]:
            raise AccountError("Add and confirm a recovery email address before setting up two-factor sign-in.")
        if row["totp_secret_enc"] and not allow_existing:
            raise AccountError("Two-factor sign-in is already on. Turn it off first.")
        if row["totp_secret_enc"] and not email_code:
            raise AccountError("Enter the code sent to your recovery email address.")
        if verified and not row["totp_secret_enc"]:
            proof = "reauth"
        else:
            proof = self.confirm_own_credentials(
                actor,
                current_password=current_password,
                email_code=email_code,
                require_email_code=bool(row["totp_secret_enc"]),
                ip=ip,
                user_agent=user_agent,
                audit_action="totp_enrollment_failed",
            )
        self._sweep_totp_enrollment(row["id"])
        secret = generate_totp_secret()
        now = _now()
        ttl = max(1, int(self.config.totp_pending_minutes)) * 60
        self.db.execute(
            "UPDATE users SET totp_pending_enc = ?, totp_pending_at = ?, updated_at = ?, totp_pending_session = ? WHERE id = ?",
            (self.secret_box.encrypt_json({"secret": secret}), now, now, sha256_hex(session_token) if session_token else "", row["id"]),
        )
        self.audit(
            actor,
            "totp_enrollment_replaced" if allow_existing else "totp_enrollment_started",
            row["id"], {"via": proof}, ip, user_agent,
        )
        issuer, uri = self._otpauth_uri(row["username"], secret, issuer)
        return {
            "secret": secret,
            "account": row["username"],
            "issuer": issuer,
            "otpauth_uri": uri,
            "expires_at": now + ttl,
        }

    def _otpauth_uri(self, username: str, secret: str, issuer: str = "") -> tuple[str, str]:
        label = (issuer or self.config.app_name).strip() or "Music Box"
        return label, (
            f"otpauth://totp/{quote(label, safe='')}:{quote(username, safe='')}"
            f"?secret={secret}&issuer={quote(label, safe='')}"
            "&period=30&digits=6&algorithm=SHA1"
        )

    def pending_totp_enrollment(self, actor: dict, session_token: str = "") -> dict:
        """The live enrollment, so the QR endpoint can render it.

        The provisioning URI carries the secret in plain text, which is why this
        is only ever served as an image from a same-origin, no-store endpoint --
        never as JSON the page could log."""
        row = self._active_row(actor)
        self._sweep_totp_enrollment(row["id"])
        fresh = self.db.fetchone(
            "SELECT totp_pending_enc, totp_pending_at, totp_pending_session FROM users WHERE id = ?", (row["id"],)
        )
        secret = self._totp_secret(fresh["totp_pending_enc"]) if fresh else ""
        if not secret or (fresh["totp_pending_session"] and fresh["totp_pending_session"] != sha256_hex(session_token)):
            return {}
        issuer, uri = self._otpauth_uri(row["username"], secret)
        return {
            "account": row["username"],
            "issuer": issuer,
            "otpauth_uri": uri,
            "expires_at": float(fresh["totp_pending_at"] or 0)
            + max(1, int(self.config.totp_pending_minutes)) * 60,
        }

    def confirm_totp_enrollment(
        self, actor: dict, *, code: str, ip: str = "", user_agent: str = "", session_token: str = ""
    ) -> dict:
        self._active_row(actor)
        with self.db.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute("SELECT * FROM users WHERE id=? AND status='active'", (actor['id'],)).fetchone()
            deadline = _now() - max(1, int(self.config.totp_pending_minutes)) * 60
            if (not row or not row['totp_pending_enc'] or row['totp_pending_at'] < deadline
                    or (row['totp_pending_session'] and row['totp_pending_session'] != sha256_hex(session_token))):
                raise AccountError('Start the two-factor setup again first.')
            secret = self._totp_secret(row['totp_pending_enc'])
            step = totp_counter(secret, code)
            if step is None:
                raise AccountError('That code did not match. Check the authenticator and try again.')
            conn.execute("UPDATE users SET totp_secret_enc=?, totp_pending_enc='', totp_pending_at=0, "
                         "totp_last_step=?, updated_at=? WHERE id=?",
                         (self.secret_box.encrypt_json({'secret': secret}), step, _now(), row['id']))
            keep = sha256_hex(session_token) if session_token else ''
            conn.execute('UPDATE user_sessions SET revoked_at=? WHERE user_id=? AND revoked_at=0 AND sid_hash<>?', (_now(), row['id'], keep))
            conn.execute('UPDATE user_sessions SET security_version=(SELECT security_version FROM users WHERE id=?) WHERE sid_hash=?', (row['id'], keep))
        self.audit(actor, "totp_enabled", row["id"], {}, ip, user_agent)
        return self.get_user(row["id"])

    def cancel_totp_enrollment(
        self, actor: dict, *, ip: str = "", user_agent: str = ""
    ) -> None:
        row = self._active_row(actor)
        self.db.execute(
            "UPDATE users SET totp_pending_enc = '', totp_pending_at = 0, updated_at = ? WHERE id = ?",
            (_now(), row["id"]),
        )
        self.audit(actor, "totp_enrollment_cancelled", row["id"], {}, ip, user_agent)

    def disable_totp(
        self,
        actor: dict,
        *,
        current_password: str = "",
        email_code: str = "",
        ip: str = "",
        user_agent: str = "",
        verified: bool = False,
        session_token: str = "",
    ) -> dict:
        """Turn 2FA off.

        The password is the proof.  An emailed code is accepted and recorded but never
        demanded: the phone that generates an authenticator code is the one thing a
        person reliably loses, and being stuck unable to remove a second factor they no
        longer have is not a protection -- it is a lockout with extra steps.  What the
        removal no longer checks is made visible instead of invisible: the proof that
        was used goes into the audit row, and an email tells the owner it happened.

        ``verified`` is the admin panel's path, where the dialog there has already
        checked a proof of its own.
        """
        row = self._active_row(actor)
        if not row["totp_secret_enc"]:
            raise AccountError("Two-factor sign-in is not on.")
        proof = self.confirm_own_credentials(
            actor, current_password=current_password, email_code=email_code,
            require_email_code=True, ip=ip, user_agent=user_agent,
            audit_action='totp_disable_failed')
        self.db.execute(
            "UPDATE users SET totp_secret_enc = '', totp_pending_enc = '', totp_pending_at = 0, "
            "totp_last_step = 0, updated_at = ? WHERE id = ?",
            (_now(), row["id"]),
        )
        keep = sha256_hex(session_token) if session_token else ""
        self.db.execute("UPDATE user_sessions SET revoked_at=? WHERE user_id=? AND revoked_at=0 AND sid_hash<>?", (_now(), row["id"], keep))
        self.db.execute("UPDATE user_sessions SET security_version=(SELECT security_version FROM users WHERE id=?) WHERE sid_hash=?", (row["id"], keep))
        self.audit(actor, "totp_disabled", row["id"], {"proof": proof}, ip, user_agent)
        return self.get_user(row["id"])

    def verify_totp_code(self, user_id: str, code: str) -> bool:
        with self.db.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute("SELECT * FROM users WHERE id = ? AND status = 'active'", (user_id or '',)).fetchone()
            now = time.time()
            if not row or row['totp_locked_until'] > now:
                return False
            secret = self._totp_secret(row['totp_secret_enc'])
            step = totp_counter(secret, code)
            if step is None or step <= row['totp_last_step']:
                failures = int(row['totp_failed_count']) + 1
                locked = now + self.config.login_window_seconds if failures >= self.config.login_max_attempts else 0
                conn.execute('UPDATE users SET totp_failed_count=?, totp_locked_until=? WHERE id=?',
                             (0 if locked else failures, locked, user_id))
                return False
            conn.execute('UPDATE users SET totp_last_step=?, totp_failed_count=0, totp_locked_until=0 WHERE id=?', (step, user_id))
            return True

    # ------------------------------------------------------------------ delete
    def set_purge_hook(self, hook) -> None:
        """Register ``hook(user_id)`` to erase an identity's non-database artifacts.

        The application owns playback state, credential files and browser profiles,
        so only it can clean them; this module must not import it.
        """
        self._purge_hook = hook

    def _purge(self, user_id: str) -> None:
        hook = self._purge_hook
        if hook is None:
            return
        try:
            hook(user_id)
        except Exception:  # noqa: BLE001 - the account is gone either way
            logger.exception("Could not purge artifacts for account %s", user_id)

    def set_artifact_hook(self, hook) -> None:
        """Register ``hook(user_id, provider)`` to erase a provider's saved artifacts.

        Disconnecting a provider deletes its row; the cookies, the Chromium profile
        and the live playback session belong to the application, so it answers with
        the list of steps it could not finish. Only then can the caller say what is
        still there instead of claiming the connection is gone.
        """
        self._artifact_hook = hook

    def _forget_artifacts(self, user_id: str, provider: str) -> list[str]:
        hook = self._artifact_hook
        if hook is None:
            return []
        try:
            return list(hook(user_id, provider) or [])
        except Exception:  # noqa: BLE001 - the row is gone either way
            logger.exception("Could not erase %s artifacts for account %s", provider, user_id)
            return ["artifacts: unexpected error"]

    def delete_own_account(
        self,
        actor: dict,
        *,
        password: str,
        confirm_username: str,
        email_code: str = "",
        dry_run: bool = False,
        ip: str = "",
        user_agent: str = "",
    ) -> str:
        """Erase an account at its own request. Returns the deleted user id.

        Hard rather than soft, because a person who deleted themselves must not be
        able to be resurrected -- and a soft-deleted row keeps its username claimed
        forever. The audit journal deliberately survives: the record that an account
        existed and closed itself is the point of an audit log.

        ``dry_run`` is the first popup of the page's four-step deletion: it proves
        the password, the username and the last-owner guard, and erases nothing. The
        emailed code belongs to the last step, so it is only asked for there.
        """
        row = self._active_row(actor)
        if not verify_password(row["password_hash"], password or ""):
            self.db.audit(
                row["id"], row["username"], "account_delete_failed", row["id"],
                json.dumps({"reason": "bad_password"}), ip, user_agent,
            )
            raise AccountError("Password is incorrect.")
        if (confirm_username or "").strip().lower() != str(row["username"]).lower():
            self.db.audit(
                row["id"], row["username"], "account_delete_failed", row["id"],
                json.dumps({"reason": "username_mismatch"}), ip, user_agent,
            )
            raise AccountError("Type your username exactly to confirm.")
        self._guard_last_owner(dict(row), {"status": "deleted"})
        if row["role"] in {"owner", "admin"} and int(self.db.scalar(
            "SELECT COUNT(*) FROM users WHERE role IN ('owner','admin') AND status = 'active'"
        ) or 0) <= 1:
            raise AccountError("The last administrator cannot delete this way. Use the admin panel.")
        if dry_run:
            return ""
        # An address on file is a second proof: the code goes to it, and the same
        # mailbox that could recover this account is the one that closes it.
        if row["email"]:
            if not (email_code or "").strip():
                raise AccountError("Enter the code that was emailed to you.")
            if not self.consume_email_code(row["id"], "reauth", email_code):
                self.db.audit(
                    row["id"], row["username"], "account_delete_failed", row["id"],
                    json.dumps({"reason": "bad_code"}), ip, user_agent,
                )
                raise AccountError("That email code did not match, or it has expired.")
        user_id = row["id"]
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE user_sessions SET revoked_at = ? WHERE user_id = ? AND revoked_at = 0",
                (_now(), user_id),
            )
        # The row itself cascades sessions, provider credentials, preferences and
        # email tokens (FOREIGN KEY ... ON DELETE CASCADE).
        self.db.execute("DELETE FROM users WHERE id = ?", (user_id,))
        self.db.audit(
            None, str(row["username"])[:120], "account_deleted_self", None,
            json.dumps({"username": row["username"], "role": row["role"]}), ip, user_agent,
        )
        self._purge(user_id)
        return user_id

    def delete_user(self, actor: dict | None, user_id: str, ip: str = "", user_agent: str = "") -> None:
        target = self.get_user(user_id)
        if not target:
            raise AccountError("User not found.")
        if not self.can_manage_user(actor, target):
            raise AccountError("You do not have permission to delete this user.")
        if actor.get("id") == user_id:
            raise AccountError("You cannot delete your own account.")
        self._guard_last_owner(target, {"status": "deleted"})
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE users SET status = 'deleted', deleted_at = ?, updated_at = ?, "
                "email = NULL, totp_secret_enc = '', totp_pending_enc = '', "
                "totp_pending_at = 0, totp_last_step = 0 WHERE id = ?",
                (_now(), _now(), user_id),
            )
            conn.execute("UPDATE user_sessions SET revoked_at = ? WHERE user_id = ?", (_now(), user_id))
            conn.execute("DELETE FROM user_provider_credentials WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM user_preferences WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM user_email_tokens WHERE user_id = ?", (user_id,))
        self.audit(actor, "user_deleted", user_id, {"username": target.get("username")}, ip, user_agent)
        self._purge(user_id)

    def permanently_delete_user(self, actor: dict | None, user_id: str,
                                confirmation: str, ip: str = "", user_agent: str = "") -> None:
        """Erase a previously deleted row; owner authorization is checked live."""
        self.legacy_owner_id()  # Never let a later owner inherit the legacy slot.
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            live_actor = conn.execute("SELECT * FROM users WHERE id=?", ((actor or {}).get("id"),)).fetchone()
            if not live_actor or live_actor['role'] != 'owner' or live_actor['status'] != 'active' or live_actor['must_change_password']:
                raise AccountError("Only an active owner can permanently delete an account.")
            if live_actor['id'] == user_id:
                raise AccountError("You cannot permanently delete your own account.")
            target = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
            if not target:
                raise AccountError("Deleted user not found.")
            if target['status'] != 'deleted':
                raise AccountError("Move the account to Deleted before permanently deleting it.")
            if not isinstance(confirmation, str) or confirmation.strip() != target['username']:
                raise AccountError("Type the deleted account's exact username to confirm.")
            # All account-owned identity records cascade, including sessions,
            # providers, email proofs, preferences and Alexa credentials/bindings.
            conn.execute("DELETE FROM users WHERE id=? AND status='deleted'", (user_id,))
            conn.execute("INSERT INTO audit_log (actor_user_id,actor_label,action,target_user_id,details_json,ip,user_agent,created_at) VALUES (?,?,?,?,?,?,?,?)",
                         (live_actor['id'],live_actor['username'],'user_permanently_deleted',user_id,'{}',ip,user_agent,_now()))
        # Keep the existing artifact cleanup/retry mechanism after the row is gone.
        self._purge(user_id)

    def legacy_owner_id(self) -> str:
        """The original playback slot stays pinned even after permanent deletion."""
        with self.db.connect() as conn:
            row = conn.execute("SELECT setting_value FROM system_settings WHERE setting_key='legacy_owner_user_id'").fetchone()
            if row:
                return str(row['setting_value'])
            original = conn.execute("SELECT id FROM users WHERE created_by='system' OR role='owner' ORDER BY CASE WHEN created_by='system' THEN 0 ELSE 1 END, created_at, id LIMIT 1").fetchone()
            if not original:
                return ''
            conn.execute("INSERT OR IGNORE INTO system_settings (setting_key,setting_value,updated_at,updated_by) VALUES ('legacy_owner_user_id',?,?, 'system')", (original['id'],_now()))
            return str(conn.execute("SELECT setting_value FROM system_settings WHERE setting_key='legacy_owner_user_id'").fetchone()['setting_value'])

    def restore_user(self, actor: dict | None, user_id: str, ip: str = "", user_agent: str = "") -> dict:
        if not actor or actor.get("role") != "owner":
            raise AccountError("Only an owner can restore a deleted account.")
        row = self.db.fetchone("SELECT * FROM users WHERE id = ? AND status = 'deleted'", (user_id,))
        if not row:
            raise AccountError("Deleted user not found.")
        self._ensure_unique(row["username"], "", exclude_id=user_id)
        self.db.execute(
            "UPDATE users SET status = 'disabled', deleted_at = 0, updated_at = ? WHERE id = ?",
            (_now(), user_id),
        )
        self.audit(actor, "user_restored", user_id, {"username": row["username"]}, ip, user_agent)
        return self.get_user(user_id)

    # ------------------------------------------------------------------- login
    def authenticate(
        self,
        username: str,
        password: str,
        ip: str = "",
        user_agent: str = "",
    ) -> tuple[dict | None, str]:
        username = (username or "").strip()
        row = self.db.fetchone(
            "SELECT * FROM users WHERE username = ? COLLATE NOCASE "
            "OR (email <> '' AND email = ? COLLATE NOCASE) LIMIT 1",
            (username, username),
        )
        if not row:
            verify_password(_DUMMY_PASSWORD_HASH, password)
            self.db.audit(
                None, username[:120] or "unknown", "login_failed", None,
                json.dumps({"reason": "unknown_user"}), ip, user_agent,
            )
            return None, "Invalid username or password."
        if row["status"] == "deleted":
            return None, "Invalid username or password."
        now = _now()
        if row["locked_until"] and row["locked_until"] > now:
            wait = max(1, int(row["locked_until"] - now))
            self.db.audit(
                None, row["username"], "login_failed", row["id"],
                json.dumps({"reason": "locked"}), ip, user_agent,
            )
            return None, f"Too many failed attempts. Try again in {wait}s."
        if not verify_password(row["password_hash"], password):
            failed = int(row["failed_login_count"] or 0) + 1
            locked_until = 0.0
            if failed >= self.config.login_max_attempts:
                locked_until = now + self.config.login_window_seconds
                failed = 0
            with self.db.connect() as conn:
                conn.execute(
                    "UPDATE users SET failed_login_count = ?, locked_until = ? WHERE id = ?",
                    (failed, locked_until, row["id"]),
                )
            self.db.audit(
                None, row["username"], "login_failed", row["id"],
                json.dumps({"reason": "bad_password"}), ip, user_agent,
            )
            return None, "Invalid username or password."
        if row["status"] != "active":
            self.db.audit(
                row["id"], row["username"], "login_blocked", row["id"],
                json.dumps({"reason": "status", "status": row["status"]}), ip, user_agent,
            )
            return None, "This account is not active. Contact an administrator."
        if (
            row["email"]
            and not row["email_verified"]
            and self._must_verify(row["created_by"])
        ):
            self.db.audit(
                row["id"], row["username"], "login_blocked", row["id"],
                json.dumps({"reason": "email_unverified"}), ip, user_agent,
            )
            return None, (
                "Verify your email address before signing in. Enter the six-digit code we "
                "sent, or ask for another one from the sign-in page."
            )
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE users SET failed_login_count = 0, locked_until = 0, "
                "last_login_at = ?, updated_at = ? WHERE id = ?",
                (now, now, row["id"]),
            )
        self.db.audit(row["id"], row["username"], "login_success", row["id"], "{}", ip, user_agent)
        return self.get_user(row["id"]), ""

    # ------------------------------------------------------------------ sessions
    def security_notice(self, user, heading, text):
        notify = getattr(self, '_security_notice', None)
        if notify and user.get('email'):
            try:
                notify(user, heading, text)
            except Exception:
                logger.exception('Could not deliver account security notice')

    def security_version(self, user_id: str) -> int:
        return int(self.db.scalar("SELECT security_version FROM users WHERE id = ? AND status = 'active'", (user_id,)) or 0)

    def invalidate_security_proofs(self, user_id: str) -> None:
        self.db.execute("UPDATE users SET security_version = security_version + 1, "
                        "totp_pending_enc = '', totp_pending_at = 0 WHERE id = ?", (user_id,))

    def create_session(self, user: dict, ip: str = "", user_agent: str = "") -> str:
        raw = new_token(32)
        now = _now()
        expires = now + self.config.session_hours * 3600
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            current = conn.execute("SELECT status, security_version FROM users WHERE id = ?", (user["id"],)).fetchone()
            if not current or current["status"] != "active" or current["security_version"] != user.get("security_version", current["security_version"]):
                raise AccountError("Sign-in changed. Start again.")
            conn.execute(
                "INSERT INTO user_sessions "
                "(sid_hash, user_id, created_at, last_seen_at, expires_at, ip, user_agent, revoked_at, security_version) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)",
                (sha256_hex(raw), user["id"], now, now, expires, (ip or "")[:64], (user_agent or "")[:300], current["security_version"]),
            )
            conn.execute(
                "DELETE FROM user_sessions WHERE expires_at < ? OR revoked_at > 0",
                (now - 86400,),
            )
        return raw

    def validate_session(self, raw_sid: str) -> tuple[dict, dict] | tuple[None, None]:
        if not raw_sid:
            return None, None
        sid_hash = sha256_hex(raw_sid)
        row = self.db.fetchone(
            "SELECT s.*, u.username, u.role, u.status AS user_status, u.email, u.must_change_password, u.security_version AS user_security_version "
            "FROM user_sessions s JOIN users u ON u.id = s.user_id WHERE s.sid_hash = ?",
            (sid_hash,),
        )
        if not row:
            return None, None
        now = _now()
        if row["revoked_at"] or row["expires_at"] <= now or row["security_version"] != row["user_security_version"]:
            return None, None
        absolute_limit = now - self.config.session_absolute_days * 86400
        if row["created_at"] < absolute_limit:
            return None, None
        if row["user_status"] != "active":
            return None, None
        # Throttle last-seen writes to avoid a DB write on every request.
        if now - float(row["last_seen_at"] or 0) > 60:
            self.db.execute(
                "UPDATE user_sessions SET last_seen_at = ? WHERE sid_hash = ?",
                (now, sid_hash),
            )
        session = {
            "sid_hash": sid_hash,
            "created_at": row["created_at"],
            "expires_at": row["expires_at"],
            "ip": row["ip"],
            "user_agent": row["user_agent"],
        }
        user = {
            "id": row["user_id"],
            "username": row["username"],
            "role": row["role"],
            "status": row["user_status"],
            "email": row["email"],
            "must_change_password": bool(row["must_change_password"]),
            "security_version": row["user_security_version"],
        }
        return session, user

    def revoke_session(self, sid_hash: str) -> None:
        self.db.execute("UPDATE user_sessions SET revoked_at = ? WHERE sid_hash = ? AND revoked_at = 0", (_now(), sid_hash))

    def revoke_all_sessions(self, actor: dict, user_id: str, ip: str = "", user_agent: str = "") -> int:
        target = self.get_user(user_id)
        if not target:
            raise AccountError("User not found.")
        if not self.can_manage_user(actor, target) and not (actor and actor.get("id") == user_id):
            raise AccountError("You do not have permission to revoke these sessions.")
        self.invalidate_security_proofs(user_id)
        count = self.db.execute(
            "UPDATE user_sessions SET revoked_at = ? WHERE user_id = ? AND revoked_at = 0",
            (_now(), user_id),
        )
        self.audit(actor, "sessions_revoked", user_id, {"count": count}, ip, user_agent)
        return count

    def revoke_own_sessions(
        self,
        actor: dict,
        *,
        keep_sid_hash: str = "",
        only_prefix: str = "",
        ip: str = "",
        user_agent: str = "",
    ) -> tuple[int, bool]:
        """Sign this account's browsers out, optionally keeping the current one.

        Returns ``(revoked, killed_current)``. ``killed_current`` is what tells the
        caller to drop its own cookie: a device list where "sign out everywhere"
        quietly kept the requester alive would be a lie.
        """
        row = self._active_row(actor)
        prefix = (only_prefix or "").strip().lower()
        current_hash = sha256_hex(keep_sid_hash) if keep_sid_hash else ""
        now = _now()
        with self.db.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            if not prefix:
                conn.execute("UPDATE users SET security_version=security_version+1, totp_pending_enc='', totp_pending_at=0 WHERE id=?", (row['id'],))
                if current_hash:
                    conn.execute('UPDATE user_sessions SET security_version=(SELECT security_version FROM users WHERE id=?) WHERE sid_hash=?', (row['id'], current_hash))
            if prefix:
                if len(prefix) < 6:
                    raise AccountError("Pick a session to sign out.")
                matches = conn.execute(
                    "SELECT sid_hash FROM user_sessions WHERE user_id = ? AND revoked_at = 0 "
                    "AND sid_hash LIKE ?",
                    (row["id"], prefix + "%"),
                ).fetchall()
                if len(matches) != 1:
                    raise AccountError("That session is not signed in anymore.")
                target = str(matches[0]["sid_hash"])
                count = conn.execute(
                    "UPDATE user_sessions SET revoked_at = ? WHERE sid_hash = ? AND revoked_at = 0",
                    (now, target),
                ).rowcount
                killed_current = bool(current_hash) and target == current_hash
            else:
                if current_hash:
                    count = conn.execute(
                        "UPDATE user_sessions SET revoked_at = ? "
                        "WHERE user_id = ? AND revoked_at = 0 AND sid_hash <> ?",
                        (now, row["id"], current_hash),
                    ).rowcount
                    killed_current = False
                else:
                    count = conn.execute(
                        "UPDATE user_sessions SET revoked_at = ? WHERE user_id = ? AND revoked_at = 0",
                        (now, row["id"]),
                    ).rowcount
                    killed_current = True
        self.audit(
            actor, "sessions_revoked_self", row["id"],
            {"count": count, "kept_current": not killed_current, "one": bool(prefix)},
            ip, user_agent,
        )
        return int(count or 0), killed_current

    def list_sessions(self, user_id: str) -> list[dict]:
        rows = self.db.fetchall(
            "SELECT sid_hash, created_at, last_seen_at, expires_at, ip, user_agent, revoked_at "
            "FROM user_sessions WHERE user_id = ? ORDER BY created_at DESC LIMIT 50",
            (user_id,),
        )
        return [dict(row) for row in rows]

    # --------------------------------------------------------------- credentials
    def provider_status(self, user_id: str) -> list[dict]:
        rows = self.db.fetchall(
            "SELECT provider, status, last_error, updated_at, last_validated_at, metadata_json "
            "FROM user_provider_credentials WHERE user_id = ? ORDER BY provider",
            (user_id,),
        )
        result = []
        for row in rows:
            item = dict(row)
            try:
                item["metadata"] = json.loads(item.pop("metadata_json") or "{}")
            except (ValueError, TypeError):
                item["metadata"] = {}
            result.append(item)
        return result

    def set_provider_credentials(
        self,
        actor: dict | None,
        user_id: str,
        provider: str,
        payload: dict,
        metadata: dict | None = None,
        status: str = "connected",
    ) -> None:
        if provider not in PROVIDERS:
            raise AccountError("Unknown provider.")
        if status not in {"connected", "expired", "error", "revoked"}:
            raise AccountError("Invalid credential status.")
        if not self.get_user(user_id):
            raise AccountError("User not found.")
        now = _now()
        encrypted = self.secret_box.encrypt_json(payload)
        metadata_json = json.dumps(metadata or {}, separators=(",", ":"), sort_keys=True)
        with self.db.connect() as conn:
            conn.execute(
                "INSERT INTO user_provider_credentials "
                "(user_id, provider, secret_enc, metadata_json, status, last_error, updated_at, last_validated_at) "
                "VALUES (?, ?, ?, ?, ?, '', ?, 0) "
                "ON CONFLICT(user_id, provider) DO UPDATE SET "
                "secret_enc = excluded.secret_enc, metadata_json = excluded.metadata_json, "
                "status = excluded.status, last_error = '', updated_at = excluded.updated_at",
                (user_id, provider, encrypted, metadata_json, status, now),
            )
        self.audit(actor, "provider_connected", user_id, {"provider": provider, "status": status}, "", "")

    def get_provider_secret(self, user_id: str, provider: str) -> dict:
        """Return the decrypted payload for one provider, or {} when unusable.

        This is the only read path that decrypts: `provider_status` deliberately
        never touches `secret_enc`, so no admin page or user listing can leak a
        session cookie. A decrypt failure means the encryption key changed or the
        row is corrupt -- either way the account falls back to the public catalog
        rather than the request failing outright."""
        if provider not in PROVIDERS:
            raise AccountError("Unknown provider.")
        row = self.db.fetchone(
            "SELECT secret_enc, status FROM user_provider_credentials "
            "WHERE user_id = ? AND provider = ?",
            (user_id, provider),
        )
        if not row or row["status"] != "connected":
            return {}
        try:
            payload = self.secret_box.decrypt_json(row["secret_enc"])
        except SecretBoxError:
            logger.error("Stored %s credential for %s could not be decrypted",
                         provider, user_id)
            return {}
        return payload if isinstance(payload, dict) else {}

    def set_provider_status(self, user_id: str, provider: str, status: str,
                            last_error: str = "") -> None:
        """Record a credential's live state without touching the secret."""
        if provider not in PROVIDERS:
            raise AccountError("Unknown provider.")
        if status not in {"connected", "expired", "error", "revoked"}:
            raise AccountError("Invalid credential status.")
        self.db.execute(
            "UPDATE user_provider_credentials SET status = ?, last_error = ?, "
            "last_validated_at = ? WHERE user_id = ? AND provider = ?",
            (status, (last_error or "")[:400], _now(), user_id, provider),
        )

    def clear_provider_credentials(
        self,
        actor: dict | None,
        user_id: str,
        provider: str,
        ip: str = "",
        user_agent: str = "",
    ) -> tuple[list[str], bool]:
        """Disconnect a provider from somebody else's account (admin door).

        ``(failures, was_linked)`` -- the caller must not claim a removal for a
        service this account never connected.
        """
        if not self.can_manage_user(actor, self.get_user(user_id)):
            raise AccountError("You do not have permission to disconnect provider credentials.")
        return self._disconnect_provider(actor, user_id, provider, ip, user_agent)

    def clear_own_provider_credentials(
        self,
        actor: dict | None,
        provider: str,
        ip: str = "",
        user_agent: str = "",
    ) -> tuple[list[str], bool]:
        """Disconnect a provider from the signed-in account (player door).

        Returns ``(failures, was_linked)``. ``can_manage_user`` is false for a plain
        user looking at their own row, so self-service needs its own entry point
        rather than a relaxed permission check that an admin door might reuse.
        """
        user = self.get_user(str((actor or {}).get("id") or ""))
        if not user or user.get("status") != "active" or not actor:
            raise AccountError("Sign in again to manage your linked services.")
        return self._disconnect_provider(actor, user["id"], provider, ip, user_agent)

    def _disconnect_provider(
        self, actor: dict | None, user_id: str, provider: str, ip: str, user_agent: str
    ) -> tuple[list[str], bool]:
        if provider not in PROVIDERS:
            raise AccountError("Unknown provider.")
        removed = self.db.execute(
            "DELETE FROM user_provider_credentials WHERE user_id = ? AND provider = ?",
            (user_id, provider),
        )
        failures = self._forget_artifacts(user_id, provider)
        # The audit says what actually happened, because "disconnected" followed by
        # a live session is exactly the lie this hook exists to end.
        self.audit(
            actor, "provider_disconnected", user_id,
            {"provider": provider, "was_linked": bool(removed),
             "artifacts": "removed" if not failures else "; ".join(failures)[:400]},
            ip, user_agent,
        )
        return failures, bool(removed)

    # ------------------------------------------------------------- email tokens
    def find_user_by_email(self, email: str) -> dict:
        email = _normalize_email(email)
        if not email:
            return {}
        return _public_user(
            self.db.fetchone(
                "SELECT * FROM users WHERE email = ? COLLATE NOCASE AND status <> 'deleted' LIMIT 1",
                (email,),
            )
        )

    def _token_ttl(self, purpose: str) -> int:
        if purpose in {"verify", "reset", "reauth"}:
            # A typed code, not a link: one million possible values, and every hour
            # the window stays open is another hour to spend guesses in.
            return self.config.email_code_minutes * 60
        if purpose == "magic":
            return self.config.email_token_magic_minutes * 60
        return self.config.email_token_invite_hours * 3600

    def create_email_token(
        self,
        user_id: str,
        purpose: str,
        email: str = "",
        ip: str = "",
        user_agent: str = "",
    ) -> str:
        """Issue the one live *link* credential for ``purpose`` and return its token.

        Only ``invite`` and ``magic`` still travel by URL.  Asking this for
        ``verify``, ``reset`` or ``reauth`` raises rather than producing a link no route can
        consume, so a half-migrated caller cannot quietly reintroduce a second door.
        """
        return self._issue_email_credential(user_id, purpose, email, ip, user_agent, False)["token"]

    def create_email_code(
        self,
        user_id: str,
        purpose: str,
        email: str = "",
        ip: str = "",
        user_agent: str = "",
    ) -> str:
        """Issue the one live typed code for ``purpose`` and return it for the email.

        This is the only path that hands back a code in plaintext, which is why it is
        separate: the caller must put the value in exactly one place (the message) and
        never in an audit row, a redirect or a page.
        """
        return self._issue_email_credential(user_id, purpose, email, ip, user_agent, True)["code"]

    def _issue_email_credential(
        self, user_id: str, purpose: str, email: str, ip: str, user_agent: str, code: bool
    ) -> dict:
        if purpose not in {"verify", "reset", "magic", "invite", "reauth"}:
            raise AccountError("Invalid email token purpose.")
        if code != (purpose in {"verify", "reset", "reauth"}):
            raise AccountError("Verification, reset and re-authentication are typed "
                               "codes; links are not.")
        user = self.get_user(user_id)
        if not user:
            raise AccountError("User not found.")
        email = _normalize_email((user.get("pending_email") if purpose == "verify" else "") or email or user.get("email") or "")
        if not email:
            raise AccountError("This account has no email address.")
        raw = new_token(32)
        # Still the primary key even for a code row: it keeps one identity scheme for
        # the table, and nothing about it is ever revealed to the visitor.
        now = _now()
        expires = now + self._token_ttl(purpose)
        digits = new_numeric_code(CODE_DIGITS) if code else ""
        # Encrypted, not hashed.  Six digits is a million values, so a digest of one
        # can be read by trying them all; the Fernet key is not in the database file.
        code_enc = self.secret_box.encrypt_json({"code": digits}) if code else ""
        with self.db.connect() as conn:
            # One live credential per purpose: asking again invalidates the previous
            # one, so forwarded messages and stale tabs stop working.
            conn.execute(
                "DELETE FROM user_email_tokens WHERE user_id = ? AND purpose = ?",
                (user_id, purpose),
            )
            conn.execute(
                "INSERT INTO user_email_tokens "
                "(token_hash, user_id, purpose, email, created_at, expires_at, used_at, ip, "
                "user_agent, code_enc, code_attempts) VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, ?, 0)",
                (
                    sha256_hex(raw), user_id, purpose, email, now, expires,
                    (ip or "")[:64], (user_agent or "")[:300], code_enc,
                ),
            )
            conn.execute(
                "UPDATE users SET last_email_at = ? WHERE id = ?",
                (now, user_id),
            )
        return {"token": raw, "code": digits}

    def _code_row(self, user_id: str, purpose: str) -> dict:
        """The one live code row for this purpose, or {} if there is no usable one.

        The same four validity rules as ``_token_row``: not spent, not expired, the
        account not deleted, and the address unchanged since it was issued -- a
        verification code for ``old@example.com`` must not confirm
        ``attacker@example.com``.  Plus the attempt ceiling, which is why a code dies
        before its window does.
        """
        if not user_id or purpose not in {"verify", "reset", "reauth"}:
            return {}
        row = self.db.fetchone(
            "SELECT t.*, u.username, CASE WHEN t.purpose = 'verify' AND u.pending_email <> '' THEN u.pending_email ELSE u.email END AS current_email, u.status AS user_status, u.role "
            "FROM user_email_tokens t JOIN users u ON u.id = t.user_id "
            "WHERE t.user_id = ? AND t.purpose = ? AND t.code_enc <> ''",
            (user_id, purpose),
        )
        if not row:
            return {}
        item = dict(row)
        if item["used_at"] or item["expires_at"] <= _now():
            return {}
        if item["user_status"] == "deleted":
            return {}
        if int(item["code_attempts"] or 0) >= max(1, self.config.email_code_attempts):
            return {}
        if _normalize_email(item.get("email")) != _normalize_email(item.get("current_email") or ""):
            return {}
        return item

    def email_code_state(self, user_id: str, purpose: str) -> dict:
        """Whether a live code exists, for the page that must say "check your inbox"."""
        row = self._code_row(user_id, purpose)
        if not row:
            return {"pending": False}
        return {
            "pending": True,
            "email": row["email"],
            "expires_at": row["expires_at"],
            "attempts_left": max(0, self.config.email_code_attempts - int(row["code_attempts"] or 0)),
        }

    def consume_email_code(self, user_id: str, purpose: str, code: str) -> dict:
        """Spend the live code, or return {} -- and never say which failure this was.

        "No such code", "expired", "guessed too often" and "wrong" are one answer,
        because the difference is exactly what tells an attacker that a username has a
        pending request.  A malformed entry costs no attempt (a fat finger is not a
        guess); a well-formed wrong one does, and reaching the ceiling spends the row
        outright so the only way forward is a fresh code.  The account is never locked
        for a wrong code: anyone who knows a username could then lock it for free.
        """
        code = (code or "").strip().replace(" ", "").replace("-", "")
        if len(code) != CODE_DIGITS or not code.isdigit():
            return {}
        row = self._code_row(user_id, purpose)
        if not row:
            return {}
        try:
            stored = (self.secret_box.decrypt_json(row["code_enc"]) or {}).get("code") or ""
        except SecretBoxError:
            # A rotated key makes this row unreadable forever, so it is spent rather
            # than left to be retried; a resend replaces it.
            self.db.execute(
                "UPDATE user_email_tokens SET used_at = ? WHERE token_hash = ? AND used_at = 0",
                (_now(), row["token_hash"]),
            )
            logger.warning("Email code could not be decrypted for account %s", user_id)
            return {}
        if not safe_equal(stored, code):
            ceiling = max(1, self.config.email_code_attempts)
            with self.db.connect() as conn:
                # One statement, incrementing the column it also tests: two parallel
                # wrong guesses must not both read "attempt 1" and so buy more guesses
                # than the ceiling promises.
                conn.execute(
                    "UPDATE user_email_tokens SET code_attempts = code_attempts + 1, "
                    "used_at = CASE WHEN code_attempts + 1 >= ? THEN ? ELSE used_at END "
                    "WHERE token_hash = ? AND used_at = 0",
                    (ceiling, _now(), row["token_hash"]),
                )
            return {}
        with self.db.connect() as conn:
            changed = conn.execute(
                "UPDATE user_email_tokens SET used_at = ? WHERE token_hash = ? AND used_at = 0",
                (_now(), row["token_hash"]),
            ).rowcount
        if not changed:
            # Two submits raced for the same code; only one may pass.
            return {}
        return {"user": self.get_user(user_id), "email": row["email"], "purpose": purpose}

    def recent_email_token_count(self, user_id: str, purpose: str, seconds: int = 60) -> int:
        return int(
            self.db.scalar(
                "SELECT COUNT(*) FROM user_email_tokens WHERE user_id = ? AND purpose = ? AND created_at > ?",
                (user_id, purpose, _now() - seconds),
            )
            or 0
        )

    def _token_row(self, raw: str, purpose: str) -> dict:
        if not raw or purpose not in {"verify", "reset", "magic", "invite"}:
            return {}
        row = self.db.fetchone(
            "SELECT t.*, u.username, u.email AS current_email, u.status AS user_status, "
            "u.role, u.email_verified "
            "FROM user_email_tokens t JOIN users u ON u.id = t.user_id "
            "WHERE t.token_hash = ? AND t.purpose = ?",
            (sha256_hex(raw), purpose),
        )
        if not row:
            return {}
        item = dict(row)
        now = _now()
        if item["used_at"] or item["expires_at"] <= now:
            return {}
        if item["user_status"] == "deleted":
            return {}
        if _normalize_email(item.get("email")) != _normalize_email(item.get("current_email") or ""):
            return {}
        return item

    def peek_email_token(self, raw: str, purpose: str) -> dict:
        item = self._token_row(raw, purpose)
        if not item:
            return {}
        return {
            "user_id": item["user_id"],
            "purpose": purpose,
            "email": item["email"],
            "expires_at": item["expires_at"],
            "username": item["username"],
        }

    def consume_email_token(self, raw: str, purpose: str) -> dict:
        item = self._token_row(raw, purpose)
        if not item:
            return {}
        now = _now()
        with self.db.connect() as conn:
            changed = conn.execute(
                "UPDATE user_email_tokens SET used_at = ? WHERE token_hash = ? AND used_at = 0",
                (now, item["token_hash"]),
            ).rowcount
        if not changed:
            return {}
        user = self.get_user(item["user_id"])
        return {"user": user, "email": item["email"], "purpose": purpose}

    def mark_email_verified(
        self,
        actor: dict | None,
        user_id: str,
        ip: str = "",
        user_agent: str = "",
        method: str = "admin",
    ) -> dict:
        target = self.get_user(user_id)
        if not target:
            raise AccountError("User not found.")
        if not self.can_manage_user(actor, target) and not (actor and actor.get("id") == user_id):
            raise AccountError("You do not have permission to verify this email address.")
        if not target.get("email"):
            raise AccountError("Add an email address first.")
        self.db.execute(
            "UPDATE users SET email_verified = 1, email_verified_at = ?, updated_at = ? WHERE id = ?",
            (_now(), _now(), user_id),
        )
        self.audit(actor, "email_verified", user_id, {"method": method}, ip, user_agent)
        return self.get_user(user_id)

    def verify_email_with_token(self, user_id: str, ip: str = "", user_agent: str = "") -> dict:
        target = self.get_user(user_id)
        if not target:
            raise AccountError("User not found.")
        if not target.get("email"):
            raise AccountError("This account has no email address.")
        now = _now()
        with self.db.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute('SELECT pending_email FROM users WHERE id=?', (user_id,)).fetchone()
            if row and row['pending_email']:
                conn.execute("UPDATE users SET email=pending_email, pending_email='', pending_email_at=0, "
                             "email_verified=1, email_verified_at=?, updated_at=? WHERE id=?", (now, now, user_id))
                conn.execute('UPDATE user_sessions SET revoked_at=? WHERE user_id=? AND revoked_at=0', (now, user_id))
            else:
                conn.execute('UPDATE users SET email_verified=1, email_verified_at=?, updated_at=? WHERE id=?', (now, now, user_id))
        self.db.audit(
            None, "email", "email_verified", user_id,
            json.dumps({"method": "email_token"}), ip, user_agent,
        )
        return self.get_user(user_id)

    def purge_email_tokens(self, user_id: str) -> None:
        self.db.execute("DELETE FROM user_email_tokens WHERE user_id = ?", (user_id,))

    # --------------------------------------------------------------- preferences
    def get_preference(self, user_id: str, key: str, default: str = "") -> str:
        row = self.db.fetchone(
            "SELECT pref_value FROM user_preferences WHERE user_id = ? AND pref_key = ?",
            (user_id, key),
        )
        return row["pref_value"] if row else default

    def set_preference(self, user_id: str, key: str, value: str) -> None:
        with self.db.connect() as conn:
            conn.execute(
                "INSERT INTO user_preferences (user_id, pref_key, pref_value, updated_at) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(user_id, pref_key) DO UPDATE SET "
                "pref_value = excluded.pref_value, updated_at = excluded.updated_at",
                (user_id, key[:80], value or "", _now()),
            )

    # -------------------------------------------------------------------- audit
    def audit(
        self,
        actor: dict | None,
        action: str,
        target_user_id: str | None = None,
        details: dict | None = None,
        ip: str = "",
        user_agent: str = "",
    ) -> None:
        self.db.audit(
            (actor or {}).get("id"),
            (actor or {}).get("username") or "system",
            action,
            target_user_id,
            json.dumps(details or {}, separators=(",", ":"), sort_keys=True),
            ip,
            user_agent,
        )

    def list_audit(
        self,
        page: int = 1,
        per_page: int = 50,
        action: str = "",
        actor: str = "",
        target: str = "",
    ) -> tuple[list[dict], int]:
        page = max(1, int(page or 1))
        per_page = min(200, max(10, int(per_page or 50)))
        clauses = ["1 = 1"]
        params: list[Any] = []
        if action:
            clauses.append("action = ?")
            params.append(action)
        if actor:
            clauses.append("(actor_label LIKE ? OR actor_user_id = ?)")
            params.extend([f"%{actor}%", actor])
        if target:
            clauses.append("target_user_id = ?")
            params.append(target)
        where = " AND ".join(clauses)
        total = int(self.db.scalar(f"SELECT COUNT(*) FROM audit_log WHERE {where}", tuple(params)) or 0)
        rows = self.db.fetchall(
            f"SELECT * FROM audit_log WHERE {where} ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
            tuple(params + [per_page, (page - 1) * per_page]),
        )
        result = []
        for row in rows:
            item = dict(row)
            try:
                item["details"] = json.loads(item.get("details_json") or "{}")
            except (ValueError, TypeError):
                item["details"] = {}
            result.append(item)
        return result, total

    def audit_actions(self) -> list[str]:
        return [
            row["action"]
            for row in self.db.fetchall(
                "SELECT DISTINCT action FROM audit_log ORDER BY action LIMIT 200"
            )
        ]

    def user_audit(self, user_id: str, limit: int = 20) -> list[dict]:
        rows = self.db.fetchall(
            "SELECT * FROM audit_log WHERE actor_user_id = ? OR target_user_id = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (user_id, user_id, limit),
        )
        result = []
        for row in rows:
            item = dict(row)
            try:
                item["details"] = json.loads(item.get("details_json") or "{}")
            except (ValueError, TypeError):
                item["details"] = {}
            result.append(item)
        return result
