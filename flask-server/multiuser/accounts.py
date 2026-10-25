"""Account, session, credential and audit domain services.

This module has no Flask imports so it can be unit-tested and, later, reused by
a worker or admin CLI without booting the whole application.
"""
from __future__ import annotations

import json
import os
import re
import time
import uuid
from typing import Any

from .config import MultiUserConfig
from .crypto import SecretBox
from .db import Database
from .security import (
    generate_password,
    hash_password,
    new_token,
    sha256_hex,
    validate_password,
    verify_password,
)

USERNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,31}$")
RESERVED_USERNAMES = {"anonymous", "system", "deleted", "root", "administrator"}
ROLES = ("owner", "admin", "user")
STATUSES = ("active", "disabled", "invited")
PROVIDERS = ("youtube", "amazon")


class AccountError(ValueError):
    """A validation or permission error safe to show to an administrator."""


def _now() -> float:
    return time.time()


def _normalize_username(username: str) -> str:
    return (username or "").strip()


def _normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def _public_user(row: Any) -> dict:
    if row is None:
        return {}
    user = dict(row)
    user.pop("password_hash", None)
    user.pop("totp_secret_enc", None)
    user.pop("failed_login_count", None)
    user.pop("locked_until", None)
    user.pop("last_email_at", None)
    user["has_totp"] = bool(dict(row).get("totp_secret_enc"))
    user["email_verified"] = bool(dict(row).get("email_verified"))
    return user


class AccountService:
    def __init__(self, db: Database, config: MultiUserConfig, secret_box: SecretBox):
        self.db = db
        self.config = config
        self.secret_box = secret_box

    # ------------------------------------------------------------------ setup
    def bootstrap(self) -> dict:
        """Create the first owner once. Never overwrites existing accounts."""
        self.db.initialize()
        existing = self.db.scalar("SELECT COUNT(*) FROM users") or 0
        if existing:
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
                "INSERT INTO users (id, username, email, display_name, password_hash, role, status, "
                "must_change_password, totp_secret_enc, failed_login_count, locked_until, created_at, "
                "updated_at, last_login_at, created_by, deleted_at, email_verified, email_verified_at, "
                "last_email_at) "
                "VALUES (?, ?, ?, ?, ?, 'owner', 'active', 1, '', 0, 0, ?, ?, 0, 'system', 0, ?, ?, 0)",
                (
                    user_id,
                    username,
                    bootstrap_email,
                    "Owner",
                    hash_password(password),
                    now,
                    now,
                    1 if bootstrap_email else 0,
                    now if bootstrap_email else 0,
                ),
            )
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
    def get_user(self, user_id: str) -> dict:
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
        clauses = ["status <> 'deleted'"]
        params: list[Any] = []
        if query:
            like = f"%{query.strip()}%"
            clauses.append("(username LIKE ? OR email LIKE ? OR display_name LIKE ?)")
            params.extend([like, like, like])
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
        display_name: str = "",
        role: str = "user",
        must_change_password: bool = True,
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
        user_id = str(uuid.uuid4())
        now = _now()
        verified = bool(email_verified and email)
        with self.db.connect() as conn:
            conn.execute(
                "INSERT INTO users (id, username, email, display_name, password_hash, role, status, "
                "must_change_password, totp_secret_enc, failed_login_count, locked_until, created_at, "
                "updated_at, last_login_at, created_by, deleted_at, email_verified, email_verified_at, "
                "last_email_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, '', 0, 0, ?, ?, 0, ?, 0, ?, ?, 0)",
                (
                    user_id,
                    username,
                    email,
                    (display_name or "").strip()[:120],
                    hash_password(password),
                    role,
                    status,
                    1 if must_change_password else 0,
                    now,
                    now,
                    actor.get("id") if actor else None,
                    1 if verified else 0,
                    now if verified else 0,
                ),
            )
        self.audit(
            actor, "user_created", user_id,
            {"username": username, "role": role, "must_change_password": must_change_password},
            ip, user_agent,
        )
        return self.get_user(user_id)

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

        allowed = {"username", "email", "display_name", "role", "status"}
        changes = {key: value for key, value in (changes or {}).items() if key in allowed}
        if not changes:
            raise AccountError("Nothing to update.")

        if "username" in changes:
            changes["username"] = self.validate_username(changes["username"])
        if "email" in changes:
            changes["email"] = self.validate_email(changes["email"])
        if "display_name" in changes:
            changes["display_name"] = (changes.get("display_name") or "").strip()[:120]
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
        display_name: str,
        email: str,
        current_password: str = "",
        ip: str = "",
        user_agent: str = "",
    ) -> dict:
        """Let a signed-in administrator edit their own display name and email.

        A changed email address requires the current password and starts
        unverified, so a hijacked session cannot silently take over password
        reset or magic-link sign-in for an address the attacker controls.
        """
        if not actor:
            raise AccountError("Sign in first.")
        target = self.get_user(actor.get("id", ""))
        if not target:
            raise AccountError("User not found.")
        display_name = (display_name or "").strip()[:120]
        email = self.validate_email(email)
        email_changed = email != (target.get("email") or "").strip().lower()
        if email_changed:
            row = self.db.fetchone(
                "SELECT password_hash FROM users WHERE id = ? AND status = 'active'",
                (target["id"],),
            )
            if not row or not verify_password(row["password_hash"], current_password):
                raise AccountError("Enter your current password to change the email address.")
            self._ensure_unique(target["username"], email, exclude_id=target["id"])
        now = _now()
        with self.db.connect() as conn:
            if email_changed:
                conn.execute(
                    "UPDATE users SET display_name = ?, email = ?, email_verified = 0, "
                    "email_verified_at = 0, updated_at = ? WHERE id = ?",
                    (display_name, email, now, target["id"]),
                )
                conn.execute("DELETE FROM user_email_tokens WHERE user_id = ?", (target["id"],))
            else:
                conn.execute(
                    "UPDATE users SET display_name = ?, updated_at = ? WHERE id = ?",
                    (display_name, now, target["id"]),
                )
        self.audit(
            actor,
            "profile_updated",
            target["id"],
            {"email_changed": email_changed, "display_name_changed": display_name != (target.get("display_name") or "")},
            ip,
            user_agent,
        )
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
    ) -> None:
        row = self.db.fetchone("SELECT * FROM users WHERE id = ? AND status = 'active'", (actor.get("id", ""),))
        if not row or not verify_password(row["password_hash"], current_password):
            raise AccountError("Current password is incorrect.")
        problem = validate_password(new_password, row["username"])
        if problem:
            raise AccountError(problem)
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE users SET password_hash = ?, must_change_password = 0, updated_at = ? WHERE id = ?",
                (hash_password(new_password), _now(), row["id"]),
            )
            conn.execute("DELETE FROM user_email_tokens WHERE user_id = ?", (row["id"],))
        self.audit(actor, "password_changed", row["id"], {"self": True}, ip, user_agent)

    def _set_password_via_email(
        self,
        user_id: str,
        password: str,
        *,
        action: str,
        activate: bool = False,
        ip: str = "",
        user_agent: str = "",
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
            conn.execute(
                "UPDATE user_sessions SET revoked_at = ? WHERE user_id = ? AND revoked_at = 0",
                (now, user_id),
            )
            conn.execute("DELETE FROM user_email_tokens WHERE user_id = ?", (user_id,))
        self.db.audit(
            None, "email", action, user_id,
            json.dumps({"method": "email", "activated": activate}), ip, user_agent,
        )
        return self.get_user(user_id)

    def reset_password_with_token(
        self, user_id: str, password: str, ip: str = "", user_agent: str = ""
    ) -> dict:
        return self._set_password_via_email(
            user_id, password, action="password_reset_completed", ip=ip, user_agent=user_agent
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

    # ------------------------------------------------------------------ delete
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
                "email = NULL, totp_secret_enc = '' WHERE id = ?",
                (_now(), _now(), user_id),
            )
            conn.execute("UPDATE user_sessions SET revoked_at = ? WHERE user_id = ?", (_now(), user_id))
            conn.execute("DELETE FROM user_provider_credentials WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM user_preferences WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM user_email_tokens WHERE user_id = ?", (user_id,))
        self.audit(actor, "user_deleted", user_id, {"username": target.get("username")}, ip, user_agent)

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
            self.config.require_email_verification
            and row["email"]
            and not row["email_verified"]
        ):
            self.db.audit(
                row["id"], row["username"], "login_blocked", row["id"],
                json.dumps({"reason": "email_unverified"}), ip, user_agent,
            )
            return None, "Verify your email address before signing in. Use the link in your inbox or ask an administrator to resend it."
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE users SET failed_login_count = 0, locked_until = 0, "
                "last_login_at = ?, updated_at = ? WHERE id = ?",
                (now, now, row["id"]),
            )
        self.db.audit(row["id"], row["username"], "login_success", row["id"], "{}", ip, user_agent)
        return self.get_user(row["id"]), ""

    # ------------------------------------------------------------------ sessions
    def create_session(self, user: dict, ip: str = "", user_agent: str = "") -> str:
        raw = new_token(32)
        now = _now()
        expires = now + self.config.session_hours * 3600
        with self.db.connect() as conn:
            conn.execute(
                "INSERT INTO user_sessions "
                "(sid_hash, user_id, created_at, last_seen_at, expires_at, ip, user_agent, revoked_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 0)",
                (sha256_hex(raw), user["id"], now, now, expires, (ip or "")[:64], (user_agent or "")[:300]),
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
            "SELECT s.*, u.username, u.role, u.status AS user_status, u.display_name, u.email "
            "FROM user_sessions s JOIN users u ON u.id = s.user_id WHERE s.sid_hash = ?",
            (sid_hash,),
        )
        if not row:
            return None, None
        now = _now()
        if row["revoked_at"] or row["expires_at"] <= now:
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
            "display_name": row["display_name"],
            "email": row["email"],
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
        count = self.db.execute(
            "UPDATE user_sessions SET revoked_at = ? WHERE user_id = ? AND revoked_at = 0",
            (_now(), user_id),
        )
        self.audit(actor, "sessions_revoked", user_id, {"count": count}, ip, user_agent)
        return count

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

    def clear_provider_credentials(
        self,
        actor: dict | None,
        user_id: str,
        provider: str,
        ip: str = "",
        user_agent: str = "",
    ) -> None:
        if provider not in PROVIDERS:
            raise AccountError("Unknown provider.")
        if not self.can_manage_user(actor, self.get_user(user_id)):
            raise AccountError("You do not have permission to disconnect provider credentials.")
        self.db.execute(
            "DELETE FROM user_provider_credentials WHERE user_id = ? AND provider = ?",
            (user_id, provider),
        )
        self.audit(actor, "provider_disconnected", user_id, {"provider": provider}, ip, user_agent)

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
        if purpose == "reset":
            return self.config.email_token_reset_minutes * 60
        if purpose == "magic":
            return self.config.email_token_magic_minutes * 60
        if purpose == "invite":
            return self.config.email_token_invite_hours * 3600
        return self.config.email_token_verify_hours * 3600

    def create_email_token(
        self,
        user_id: str,
        purpose: str,
        email: str = "",
        ip: str = "",
        user_agent: str = "",
    ) -> str:
        if purpose not in {"verify", "reset", "magic", "invite"}:
            raise AccountError("Invalid email token purpose.")
        user = self.get_user(user_id)
        if not user:
            raise AccountError("User not found.")
        email = _normalize_email(email or user.get("email") or "")
        if not email:
            raise AccountError("This account has no email address.")
        raw = new_token(32)
        now = _now()
        expires = now + self._token_ttl(purpose)
        with self.db.connect() as conn:
            # One live token per purpose: requesting a new link invalidates the
            # previous one, so forwarded/old messages stop working.
            conn.execute(
                "DELETE FROM user_email_tokens WHERE user_id = ? AND purpose = ?",
                (user_id, purpose),
            )
            conn.execute(
                "INSERT INTO user_email_tokens "
                "(token_hash, user_id, purpose, email, created_at, expires_at, used_at, ip, user_agent) "
                "VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)",
                (sha256_hex(raw), user_id, purpose, email, now, expires, (ip or "")[:64], (user_agent or "")[:300]),
            )
            conn.execute(
                "UPDATE users SET last_email_at = ? WHERE id = ?",
                (now, user_id),
            )
        return raw

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
            "u.role, u.display_name, u.email_verified "
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
        self.db.execute(
            "UPDATE users SET email_verified = 1, email_verified_at = ?, updated_at = ? WHERE id = ?",
            (now, now, user_id),
        )
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
