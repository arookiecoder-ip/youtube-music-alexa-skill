"""One email template set, two doors.

The administrator panel and the player-facing account pages issue the same
tokens, so the copy, the link layout and the audit vocabulary live here rather
than being written twice and drifting.  ``base`` is the only difference:
``/admin`` for administrator links, ``''`` for the player's own.
"""
from __future__ import annotations

from flask import flash, request

from .mailer import render_email

# Audit action suffix per purpose, so both doors record the same events.
AUDIT_ACTION = {
    "verify": "verification_email",
    "reset": "password_reset_email",
    "magic": "magic_link_email",
    "invite": "invitation_email",
}

TOKEN_PATHS = {
    "verify": "/verify-email/",
    "reset": "/reset-password/",
    "magic": "/magic/",
    "invite": "/invite/",
}

COPY = {
    "verify": (
        "Verify your email address",
        "Confirm this email address to finish securing your Music Box account.",
        "Verify email",
        "This verification link expires in {minutes} minutes.",
        "",
    ),
    "reset": (
        "Reset your Music Box password",
        "We received a request to reset the password for your Music Box account.",
        "Choose a new password",
        "This password reset link expires in {minutes} minutes and can be used once.",
        "If you did not request a reset, no action is needed — your password has not changed.",
    ),
    "magic": (
        "Sign in to Music Box",
        "Use the button below to sign in to the administrator panel. This link works once.",
        "Sign in now",
        "This sign-in link expires in {minutes} minutes.",
        "Do not forward this email: anyone with the link can sign in.",
    ),
    "invite": (
        "You're invited to Music Box",
        "An administrator created a Music Box account for you. Choose a password to activate it.",
        "Activate your account",
        "This invitation expires in {hours} hours.",
        "If you were not expecting this invitation, you can ignore this email.",
    ),
}


def token_path(purpose: str, token: str, base: str = "") -> str:
    return f"{base}{TOKEN_PATHS[purpose]}{token}"


def send_token_email(mu, user: dict, purpose: str, token: str, *, base: str = "",
                     actor: dict | None = None, ip: str = "", user_agent: str = ""):
    """Render, deliver and audit one token email. Returns the Mailer result."""
    heading, intro, label, expiry, warning = COPY[purpose]
    if purpose == "invite":
        expiry = expiry.format(hours=mu.config.email_token_invite_hours)
    elif purpose == "verify":
        expiry = expiry.format(minutes=mu.config.email_token_verify_hours * 60)
    elif purpose == "reset":
        expiry = expiry.format(minutes=mu.config.email_token_reset_minutes)
    else:
        expiry = expiry.format(minutes=mu.config.email_token_magic_minutes)
    url = mu.absolute_url(token_path(purpose, token, base), request.url_root)
    subject = f"{mu.config.app_name}: {heading}"
    text, html = render_email(
        app_name=mu.config.app_name,
        heading=heading,
        intro=intro,
        action_label=label,
        action_url=url,
        expiry=expiry,
        warning=warning,
        footer=f"Sent by {mu.config.app_name} at {mu.config.public_base_url or request.host}.",
    )
    result = mu.mailer.send(user["email"], subject, text, html)
    mu.accounts.audit(
        actor,
        f"{AUDIT_ACTION[purpose]}_{'sent' if result.ok else 'failed'}",
        user["id"],
        {
            "delivery": result.status,
            "outbox": bool(result.outbox_path),
            "error": (result.detail or "")[:200] if not result.ok else "",
        },
        ip,
        user_agent,
    )
    return result


def flash_mail(mu, result, description: str, email: str = "") -> None:
    if result.status == "sent":
        flash(f"{description} sent to {email}.", "success")
    elif result.status == "outbox":
        flash(
            f"SMTP is not configured, so the {description.lower()} was written to the local outbox "
            f"({mu.mailer.config.outbox_dir}). Configure MULTIUSER_SMTP_* to deliver it.",
            "warning",
        )
    else:
        flash(f"Could not send the {description.lower()}: {result.detail}", "error")
