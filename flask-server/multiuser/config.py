"""Configuration for the additive multi-user layer.

The layer is deliberately opt-in.  When ``MULTI_USER_ENABLED`` is unset (the
default), none of its routes are registered and the existing single-owner
application behaves exactly as before.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default
    return value if value >= minimum else default


@dataclass
class MultiUserConfig:
    """Everything the multi-user layer needs, with no import of server.py."""

    enabled: bool = False
    db_path: str = "data.db"
    admin_username: str = "admin"
    admin_password: str = ""
    admin_email: str = ""
    encryption_key: str = ""
    session_hours: int = 12
    session_absolute_days: int = 7
    login_max_attempts: int = 5
    login_window_seconds: int = 900
    request_max_per_minute: int = 180
    backup_dir: str = ""
    secure_cookies: bool = True
    app_name: str = "Music Box"
    base_path: str = "/admin"
    is_test_environment: bool = False
    public_base_url: str = ""
    require_email_verification: bool = True
    magic_link_enabled: bool = True
    email_token_reset_minutes: int = 60
    email_token_verify_hours: int = 24
    email_token_magic_minutes: int = 15
    email_token_invite_hours: int = 72
    self_signup_enabled: bool = True
    max_self_signups_per_day: int = 25
    signup_min_seconds: int = 3
    totp_pending_minutes: int = 30
    extra: dict = field(default_factory=dict)

    @classmethod
    def from_env(cls) -> "MultiUserConfig":
        db_path = os.environ.get(
            "DB_FILE",
            os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data.db"),
        )
        default_backup_dir = os.path.join(os.path.dirname(os.path.abspath(db_path)), "backups")
        return cls(
            enabled=env_bool("MULTI_USER_ENABLED"),
            db_path=db_path,
            admin_username=(
                os.environ.get("MULTIUSER_ADMIN_USER")
                or os.environ.get("ADMIN_USERNAME")
                or "admin"
            ).strip(),
            admin_password=(
                os.environ.get("MULTIUSER_ADMIN_PASSWORD")
                or os.environ.get("ADMIN_PASSWORD")
                or ""
            ),
            admin_email=(os.environ.get("MULTIUSER_ADMIN_EMAIL") or "").strip(),
            encryption_key=(
                os.environ.get("MULTIUSER_ENCRYPTION_KEY")
                or os.environ.get("ADMIN_ENCRYPTION_KEY")
                or ""
            ).strip(),
            session_hours=_env_int("MULTIUSER_SESSION_HOURS", 12),
            session_absolute_days=_env_int("MULTIUSER_SESSION_ABSOLUTE_DAYS", 7),
            login_max_attempts=_env_int("MULTIUSER_LOGIN_MAX_ATTEMPTS", 5),
            login_window_seconds=_env_int("MULTIUSER_LOGIN_WINDOW_SECONDS", 900),
            request_max_per_minute=_env_int("MULTIUSER_REQUEST_MAX_PER_MINUTE", 180),
            backup_dir=os.environ.get("MULTIUSER_BACKUP_DIR") or default_backup_dir,
            secure_cookies=(
                env_bool("COOKIE_INSECURE") is False
                and os.environ.get("PUBLIC_BASE_URL", "").lower().startswith("https://")
            ),
            app_name=os.environ.get("MULTIUSER_APP_NAME", "Music Box"),
            base_path="/admin",
            is_test_environment=env_bool("MULTIUSER_TEST_ENVIRONMENT"),
            public_base_url=(
                os.environ.get("MULTIUSER_PUBLIC_BASE_URL")
                or os.environ.get("PUBLIC_BASE_URL", "")
            ).strip().rstrip("/"),
            require_email_verification=env_bool("MULTIUSER_REQUIRE_EMAIL_VERIFICATION", True),
            magic_link_enabled=env_bool("MULTIUSER_MAGIC_LINK_ENABLED", True),
            email_token_reset_minutes=_env_int("MULTIUSER_RESET_TOKEN_MINUTES", 60),
            email_token_verify_hours=_env_int("MULTIUSER_VERIFY_TOKEN_HOURS", 24),
            email_token_magic_minutes=_env_int("MULTIUSER_MAGIC_TOKEN_MINUTES", 15),
            email_token_invite_hours=_env_int("MULTIUSER_INVITE_TOKEN_HOURS", 72),
            self_signup_enabled=env_bool("MULTIUSER_SELF_SIGNUP_ENABLED", True),
            max_self_signups_per_day=_env_int(
                "MULTIUSER_MAX_SELF_SIGNUPS_PER_DAY", 25, minimum=0
            ),
            signup_min_seconds=_env_int("MULTIUSER_SIGNUP_MIN_SECONDS", 3),
            totp_pending_minutes=_env_int("MULTIUSER_TOTP_PENDING_MINUTES", 30),
        )

    def validate(self) -> list[str]:
        """Return human-readable configuration problems (never echo secrets)."""
        problems: list[str] = []
        if not self.db_path:
            problems.append("DB_FILE is empty")
        if not self.admin_username:
            problems.append("MULTIUSER_ADMIN_USER is empty")
        if self.session_hours < 1:
            problems.append("MULTIUSER_SESSION_HOURS must be >= 1")
        if self.login_max_attempts < 1:
            problems.append("MULTIUSER_LOGIN_MAX_ATTEMPTS must be >= 1")
        return problems
