"""One email template set, two doors, two kinds of credential.

The administrator panel and the player-facing account pages issue the same tokens,
so the copy, the layout and the audit vocabulary live here rather than being
written twice and drifting.  ``base`` is the only difference between the doors:
``/admin`` for administrator links, ``''`` for the player's own.

Verification and reset are **typed codes** now, which is a different message shape:
nothing in a code email may be clickable, because a link is what this replaced.
Invitation and the administrator's sign-in link are still URLs, so they still go
through :func:`send_token_email`.  A notice that something has already changed carries
neither, and has no reason to: :func:`send_notice` says what happened and when.
"""
from __future__ import annotations

from flask import flash, request

from .mailer import render_alert_email, render_code_email, render_email

# Audit action suffix per purpose, so both doors record the same events.
AUDIT_ACTION = {
    "verify": "verification_code_email",
    "reset": "password_reset_code_email",
    "reauth": "reauth_code_email",
    "magic": "magic_link_email",
    "invite": "invitation_email",
}

# Only the purposes that still travel by URL.  A purpose absent from this map has
# no path to build, which is what stops a half-migrated caller from issuing a
# verification link that no route would ever accept.
TOKEN_PATHS = {
    "magic": "/magic/",
    "invite": "/invite/",
}

# heading, intro, action label, expiry, warning
COPY = {
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

# heading, intro, expiry, warning.  ``intro`` is formatted with ``{where}``.
CODE_COPY = {
    "magic": (
        "Sign in to Music Box",
        "Enter this code on {where} to sign in to your Music Box account.",
        "This code expires in {minutes} minutes, works once, and stops working after {attempts} wrong tries.",
        "If you did not request this code, ignore this email. Never share your code.",
    ),
    "verify": (
        "Verify your email address",
        "Enter this code on {where} to confirm the address on your Music Box account.",
        "This code expires in {minutes} minutes, works once, and stops working after "
        "{attempts} wrong tries.",
        "Do not share this code with anyone. Nobody from Music Box will ever ask you "
        "for it in a message you did not start yourself.",
    ),
    "reset": (
        "Reset your Music Box password",
        "Enter this code on {where} to choose a new password.",
        "This code expires in {minutes} minutes, works once, and stops working after "
        "{attempts} wrong tries.",
        "If you did not request a reset, no action is needed — your password has not changed.",
    ),
    "reauth": (
        "Confirm a security change",
        "Enter this code on {where} to prove this is you before the change to your "
        "Music Box sign-in details is made.",
        "This code expires in {minutes} minutes, works once, and stops working after "
        "{attempts} wrong tries.",
        "If you did not ask for this change, ignore this email and change your password.",
    ),
}


def token_path(purpose: str, token: str, base: str = "") -> str:
    return f"{base}{TOKEN_PATHS[purpose]}{token}"


def _subject(mu, heading: str) -> str:
    return f"{mu.config.app_name}: {heading}"


def _footer(mu) -> str:
    return f"Sent by {mu.config.app_name} at {mu.config.public_base_url or request.host}."


def _deliver_and_audit(mu, user: dict, purpose: str, text: str, html: str, subject: str,
                       actor: dict | None, ip: str, user_agent: str):
    result = mu.mailer.send(user["email"], subject, text, html)
    # The code itself is never in these arguments: an audit row is readable by
    # anybody with the panel, and a live code is a password.
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


def send_token_email(mu, user: dict, purpose: str, token: str, *, base: str = "",
                     actor: dict | None = None, ip: str = "", user_agent: str = ""):
    """Render, deliver and audit one token email. Returns the Mailer result."""
    heading, intro, label, expiry, warning = COPY[purpose]
    if purpose == "invite":
        expiry = expiry.format(hours=mu.config.email_token_invite_hours)
    else:
        expiry = expiry.format(minutes=mu.config.email_token_magic_minutes)
    url = mu.absolute_url(token_path(purpose, token, base), request.url_root)
    text, html = render_email(
        app_name=mu.config.app_name,
        heading=heading,
        intro=intro,
        action_label=label,
        action_url=url,
        expiry=expiry,
        warning=warning,
        footer=_footer(mu),
    )
    return _deliver_and_audit(mu, user, purpose, text, html, _subject(mu, heading),
                              actor, ip, user_agent)


def send_code_email(mu, user: dict, purpose: str, code: str, *, where: str = "this page",
                    actor: dict | None = None, ip: str = "", user_agent: str = ""):
    """Render, deliver and audit one numeric-code email. Returns the Mailer result.

    ``where`` is words, not a link: "the sign-up page", "your account page".  A
    message that told the reader *where* without being able to *take* them there is
    the property that made this switch worth making.
    """
    heading, intro, expiry, warning = CODE_COPY[purpose]
    minutes = max(1, int(mu.config.email_token_magic_minutes if purpose == "magic" else mu.config.email_code_minutes))
    text, html = render_code_email(
        app_name=mu.config.app_name,
        heading=heading,
        intro=intro.format(where=where),
        code=code,
        expiry=expiry.format(minutes=minutes, attempts=mu.config.email_code_attempts),
        where=where,
        warning=warning,
        footer=_footer(mu),
    )
    return _deliver_and_audit(mu, user, purpose, text, html, _subject(mu, heading),
                              actor, ip, user_agent)


def send_notice(mu, user: dict, heading: str, intro: str, rows: tuple = (), note: str = "",
                *, audit: str = "security_notice", actor: dict | None = None,
                ip: str = "", user_agent: str = ""):
    """Deliver a report of something that has already happened.

    A third kind of message next to the code and the link: it carries no credential at
    all, so there is nothing in it to type, click or leak.  It exists because a change
    the owner did not make is only discoverable if the account tells them twice -- the
    audit row nobody reads, and a mail they do.
    """
    text, html = render_alert_email(
        app_name=mu.config.app_name,
        heading=heading,
        intro=intro,
        rows=rows,
        note=note,
        footer=_footer(mu),
        fine_print=(
            f"Sent automatically by {mu.config.app_name} because something on your "
            "account changed."
        ),
        trailing=(
            "If this wasn't you, change your password -- and turn two-factor sign-in "
            "back on if it was switched off."
        ),
    )
    result = mu.mailer.send(user["email"], _subject(mu, heading), text, html)
    mu.accounts.audit(
        actor,
        f"{audit}_{'sent' if result.ok else 'failed'}",
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


def mail_message(mu, result, description: str, email: str = "") -> tuple[str, str]:
    """(category, text) for one send result, whether it is flashed or toasted."""
    if result.status == "sent":
        return "success", f"{description} sent to {email}."
    if result.status == "outbox":
        return "warning", (
            f"SMTP is not configured, so the {description.lower()} was written to the local outbox "
            f"({mu.mailer.config.outbox_dir}). Configure MULTIUSER_SMTP_* to deliver it."
        )
    return "error", f"Could not send the {description.lower()}: {result.detail}"


def flash_mail(mu, result, description: str, email: str = "") -> None:
    category, message = mail_message(mu, result, description, email)
    flash(message, category)
