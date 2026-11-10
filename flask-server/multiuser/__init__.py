"""Additive multi-user layer for Music Box.

Usage from the existing application::

    from multiuser import init_multiuser
    init_multiuser(app)

The call is a no-op unless ``MULTI_USER_ENABLED=1``.  Initialization failures
are caught by the caller so a problem in this optional layer can never stop the
single-owner application from booting.
"""
from __future__ import annotations

from .admin import MultiUser
from .config import MultiUserConfig

# The pages the app's sign-up, verification and recovery buttons call, plus the
# account page and the owner panel.  Named here so a deployment that forgot the
# flag says which URLs will answer 404 instead of leaving that to be discovered
# on a phone.
SELF_SERVICE_ROUTES = (
    "/signup",
    "/verify",
    "/resend-code",
    "/forgot-password",
    "/reset-password",
    "/account",
    "/admin",
)


def missing_self_service_routes(app) -> list[str]:
    """Which account pages this app never mounted; empty once the layer is on."""
    rules = [str(rule.rule) for rule in app.url_map.iter_rules()]
    return [
        path for path in SELF_SERVICE_ROUTES
        if not any(rule == path or rule.startswith(path + "/") for rule in rules)
    ]


def init_multiuser(app, config: MultiUserConfig | None = None) -> MultiUser | None:
    config = config or MultiUserConfig.from_env()
    if not config.enabled:
        missing = missing_self_service_routes(app)
        app.logger.warning(
            "[multiuser] disabled: %d account route(s) not mounted (%s). "
            "Set MULTI_USER_ENABLED=1 to serve them; while off, the native app's "
            "sign-up, verification and recovery buttons and the web remote's "
            "signup/forgot links have nothing to open.",
            len(missing),
            ", ".join(missing) or "none",
        )
        return None
    layer = MultiUser(config, app_secret=app.secret_key, logger=app.logger)
    layer.init_app(app)
    missing = missing_self_service_routes(app)
    if missing:
        app.logger.error(
            "[multiuser] enabled but these account routes are missing: %s",
            ", ".join(missing),
        )
    return layer


__all__ = [
    "MultiUser",
    "MultiUserConfig",
    "init_multiuser",
    "missing_self_service_routes",
    "SELF_SERVICE_ROUTES",
]
