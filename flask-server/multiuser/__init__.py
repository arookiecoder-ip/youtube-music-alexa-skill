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


def init_multiuser(app, config: MultiUserConfig | None = None) -> MultiUser | None:
    config = config or MultiUserConfig.from_env()
    if not config.enabled:
        app.logger.info(
            "[multiuser] disabled (set MULTI_USER_ENABLED=1 to enable the admin panel)"
        )
        return None
    layer = MultiUser(config, app_secret=app.secret_key, logger=app.logger)
    layer.init_app(app)
    return layer


__all__ = ["MultiUser", "MultiUserConfig", "init_multiuser"]
