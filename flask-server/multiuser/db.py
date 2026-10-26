"""SQLite schema and migration runner for the multi-user layer.

All statements are additive/idempotent and live in separate tables from the
existing single-owner tables (``web_sessions``, ``kv``, ``jam_tokens`` and
``playlist_tracks``).  This is what lets the feature ship disabled without
touching the working app.
"""
from __future__ import annotations

import contextlib
import os
import sqlite3
import time
from typing import Any, Iterable

SCHEMA_VERSION = 3

_SCHEMA_V1 = (
    """
    CREATE TABLE IF NOT EXISTS users (
        id TEXT PRIMARY KEY,
        username TEXT NOT NULL COLLATE NOCASE UNIQUE,
        email TEXT COLLATE NOCASE,
        display_name TEXT NOT NULL DEFAULT '',
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'user'
            CHECK (role IN ('owner', 'admin', 'user')),
        status TEXT NOT NULL DEFAULT 'active'
            CHECK (status IN ('active', 'disabled', 'invited', 'deleted')),
        must_change_password INTEGER NOT NULL DEFAULT 1,
        totp_secret_enc TEXT NOT NULL DEFAULT '',
        failed_login_count INTEGER NOT NULL DEFAULT 0,
        locked_until REAL NOT NULL DEFAULT 0,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL,
        last_login_at REAL NOT NULL DEFAULT 0,
        created_by TEXT,
        deleted_at REAL NOT NULL DEFAULT 0
    )
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email_unique "
    "ON users(email COLLATE NOCASE) WHERE email IS NOT NULL AND email <> ''",
    "CREATE INDEX IF NOT EXISTS idx_users_status ON users(status)",
    "CREATE INDEX IF NOT EXISTS idx_users_role ON users(role)",
    """
    CREATE TABLE IF NOT EXISTS user_sessions (
        sid_hash TEXT PRIMARY KEY,
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        created_at REAL NOT NULL,
        last_seen_at REAL NOT NULL,
        expires_at REAL NOT NULL,
        ip TEXT NOT NULL DEFAULT '',
        user_agent TEXT NOT NULL DEFAULT '',
        revoked_at REAL NOT NULL DEFAULT 0
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_user_sessions_user ON user_sessions(user_id)",
    "CREATE INDEX IF NOT EXISTS idx_user_sessions_expiry ON user_sessions(expires_at)",
    """
    CREATE TABLE IF NOT EXISTS user_provider_credentials (
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        provider TEXT NOT NULL CHECK (provider IN ('youtube', 'amazon')),
        secret_enc TEXT NOT NULL,
        metadata_json TEXT NOT NULL DEFAULT '{}',
        status TEXT NOT NULL DEFAULT 'connected'
            CHECK (status IN ('connected', 'expired', 'error', 'revoked')),
        last_error TEXT NOT NULL DEFAULT '',
        updated_at REAL NOT NULL,
        last_validated_at REAL NOT NULL DEFAULT 0,
        PRIMARY KEY (user_id, provider)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS user_preferences (
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        pref_key TEXT NOT NULL,
        pref_value TEXT NOT NULL DEFAULT '',
        updated_at REAL NOT NULL,
        PRIMARY KEY (user_id, pref_key)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS user_invites (
        token_hash TEXT PRIMARY KEY,
        email TEXT NOT NULL DEFAULT '',
        role TEXT NOT NULL DEFAULT 'user'
            CHECK (role IN ('owner', 'admin', 'user')),
        created_by TEXT,
        created_at REAL NOT NULL,
        expires_at REAL NOT NULL,
        used_at REAL NOT NULL DEFAULT 0
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS audit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        actor_user_id TEXT,
        actor_label TEXT NOT NULL DEFAULT '',
        action TEXT NOT NULL,
        target_user_id TEXT,
        details_json TEXT NOT NULL DEFAULT '{}',
        ip TEXT NOT NULL DEFAULT '',
        user_agent TEXT NOT NULL DEFAULT '',
        created_at REAL NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_log(created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_audit_actor ON audit_log(actor_user_id)",
    "CREATE INDEX IF NOT EXISTS idx_audit_target ON audit_log(target_user_id)",
    "CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_log(action)",
    """
    CREATE TABLE IF NOT EXISTS multiuser_schema_migrations (
        version INTEGER PRIMARY KEY,
        applied_at REAL NOT NULL
    )
    """,
)

# Version 2 adds email identity: verification state and single-use tokens for
# email verification, password reset, invitations and passwordless sign-in.
_SCHEMA_V2_TABLES = (
    """
    CREATE TABLE IF NOT EXISTS user_email_tokens (
        token_hash TEXT PRIMARY KEY,
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        purpose TEXT NOT NULL CHECK (purpose IN ('verify', 'reset', 'magic', 'invite')),
        email TEXT NOT NULL DEFAULT '',
        created_at REAL NOT NULL,
        expires_at REAL NOT NULL,
        used_at REAL NOT NULL DEFAULT 0,
        ip TEXT NOT NULL DEFAULT '',
        user_agent TEXT NOT NULL DEFAULT ''
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_email_tokens_user ON user_email_tokens(user_id, purpose)",
    "CREATE INDEX IF NOT EXISTS idx_email_tokens_expiry ON user_email_tokens(expires_at)",
)

# Version 3 makes an account self-serviceable.  A two-factor secret under
# enrollment must not live in ``totp_secret_enc``: that column's truthiness *is*
# "second factor enabled" everywhere the code reads it, so a half-finished
# enrollment would start rejecting logins for a code nobody has set yet.
_SCHEMA_V3_COLUMNS = (
    ("totp_pending_enc", "ALTER TABLE users ADD COLUMN totp_pending_enc TEXT NOT NULL DEFAULT ''"),
    ("totp_pending_at", "ALTER TABLE users ADD COLUMN totp_pending_at REAL NOT NULL DEFAULT 0"),
    ("totp_last_step", "ALTER TABLE users ADD COLUMN totp_last_step INTEGER NOT NULL DEFAULT 0"),
)


class Database:
    def __init__(self, path: str):
        self.path = path
        self._initialized = False

    @contextlib.contextmanager
    def connect(self) -> Iterable[sqlite3.Connection]:
        directory = os.path.dirname(os.path.abspath(self.path))
        if directory:
            os.makedirs(directory, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=15.0)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA busy_timeout = 15000")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def initialize(self) -> None:
        if self._initialized:
            return
        with self.connect() as conn:
            for statement in _SCHEMA_V1:
                conn.execute(statement)
            applied = {
                row["version"]
                for row in conn.execute("SELECT version FROM multiuser_schema_migrations")
            }
            if 1 not in applied:
                conn.execute(
                    "INSERT INTO multiuser_schema_migrations (version, applied_at) VALUES (1, ?)",
                    (time.time(),),
                )
            if 2 not in applied:
                columns = {
                    row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()
                }
                if "email_verified" not in columns:
                    conn.execute(
                        "ALTER TABLE users ADD COLUMN email_verified INTEGER NOT NULL DEFAULT 0"
                    )
                if "email_verified_at" not in columns:
                    conn.execute(
                        "ALTER TABLE users ADD COLUMN email_verified_at REAL NOT NULL DEFAULT 0"
                    )
                if "last_email_at" not in columns:
                    conn.execute(
                        "ALTER TABLE users ADD COLUMN last_email_at REAL NOT NULL DEFAULT 0"
                    )
                for statement in _SCHEMA_V2_TABLES:
                    conn.execute(statement)
                conn.execute(
                    "INSERT INTO multiuser_schema_migrations (version, applied_at) VALUES (2, ?)",
                    (time.time(),),
                )
            if 3 not in applied:
                columns = {
                    row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()
                }
                for name, statement in _SCHEMA_V3_COLUMNS:
                    if name not in columns:
                        conn.execute(statement)
                # The daily self-signup cap reads this on every anonymous
                # registration, so it has to be an index rather than a scan.
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_users_created_by "
                    "ON users(created_by, created_at)"
                )
                conn.execute(
                    "INSERT INTO multiuser_schema_migrations (version, applied_at) VALUES (3, ?)",
                    (time.time(),),
                )
            now = time.time()
            # Bound the audit journal so a long-lived install cannot grow it
            # without limit. 90 days is intentionally conservative.
            conn.execute("DELETE FROM audit_log WHERE created_at < ?", (now - (90 * 86400),))
            conn.execute("DELETE FROM user_sessions WHERE expires_at < ?", (now - 86400,))
            conn.execute("DELETE FROM user_email_tokens WHERE expires_at < ?", (now - 86400,))
        self._initialized = True

    def fetchone(self, sql: str, params: tuple = ()) -> sqlite3.Row | None:
        with self.connect() as conn:
            return conn.execute(sql, params).fetchone()

    def fetchall(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return list(conn.execute(sql, params).fetchall())

    def execute(self, sql: str, params: tuple = ()) -> int:
        with self.connect() as conn:
            cursor = conn.execute(sql, params)
            return cursor.rowcount

    def scalar(self, sql: str, params: tuple = ()) -> Any:
        row = self.fetchone(sql, params)
        return row[0] if row is not None else None

    def audit(
        self,
        actor_user_id: str | None,
        actor_label: str,
        action: str,
        target_user_id: str | None = None,
        details: str = "{}",
        ip: str = "",
        user_agent: str = "",
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO audit_log "
                "(actor_user_id, actor_label, action, target_user_id, details_json, ip, user_agent, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    actor_user_id,
                    (actor_label or "")[:120],
                    action[:80],
                    target_user_id,
                    details or "{}",
                    (ip or "")[:64],
                    (user_agent or "")[:300],
                    time.time(),
                ),
            )

    def counts(self) -> dict[str, int]:
        result: dict[str, int] = {}
        for key, sql in {
            "total": "SELECT COUNT(*) FROM users WHERE status <> 'deleted'",
            "active": "SELECT COUNT(*) FROM users WHERE status = 'active'",
            "disabled": "SELECT COUNT(*) FROM users WHERE status = 'disabled'",
            "deleted": "SELECT COUNT(*) FROM users WHERE status = 'deleted'",
            "owners": "SELECT COUNT(*) FROM users WHERE role = 'owner' AND status = 'active'",
            "admins": "SELECT COUNT(*) FROM users WHERE role IN ('owner', 'admin') AND status = 'active'",
            "sessions": "SELECT COUNT(*) FROM user_sessions WHERE revoked_at = 0 AND expires_at > ?",
            "credentials": "SELECT COUNT(*) FROM user_provider_credentials WHERE status = 'connected'",
            "audit_events": "SELECT COUNT(*) FROM audit_log",
        }.items():
            params = (time.time(),) if "?" in sql else ()
            result[key] = int(self.scalar(sql, params) or 0)
        return result

    def schema_versions(self) -> list[sqlite3.Row]:
        return self.fetchall("SELECT version, applied_at FROM multiuser_schema_migrations ORDER BY version")

    def file_size(self) -> int:
        try:
            return os.path.getsize(self.path)
        except OSError:
            return 0

    def integrity_check(self) -> str:
        try:
            return str(self.scalar("PRAGMA quick_check") or "unknown")
        except sqlite3.Error as exc:
            return f"error: {exc}"

    def backup_to(self, destination: str) -> str:
        """Consistent online backup using SQLite's backup API."""
        os.makedirs(os.path.dirname(os.path.abspath(destination)) or ".", exist_ok=True)
        source = sqlite3.connect(self.path, timeout=30.0)
        target = sqlite3.connect(destination)
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()
        try:
            os.chmod(destination, 0o600)
        except OSError:
            pass
        return destination
