"""SMTP delivery for verification, reset, invitation and magic-link emails.

Standard library only.  When SMTP is not configured the message is written as
an ``.eml`` file to a local outbox (never logged), so the flow remains testable
on a private server without leaking one-time tokens into application logs.
"""
from __future__ import annotations

import os
import re
import smtplib
import ssl
import time
import uuid
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from typing import Optional

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class MailResult:
    status: str  # sent | outbox | disabled | error
    detail: str = ""
    outbox_path: str = ""
    message_id: str = ""

    @property
    def delivered(self) -> bool:
        return self.status == "sent"

    @property
    def ok(self) -> bool:
        return self.status in {"sent", "outbox"}


@dataclass
class MailerConfig:
    host: str = ""
    port: int = 587
    security: str = "starttls"  # starttls | ssl | none
    username: str = ""
    password: str = ""
    from_address: str = ""
    from_name: str = "Music Box"
    reply_to: str = ""
    timeout: int = 12
    outbox_dir: str = ""
    redirect_to: str = ""
    suppress_send: bool = False

    @classmethod
    def from_env(cls, default_outbox_dir: str = "") -> "MailerConfig":
        security = (os.environ.get("MULTIUSER_SMTP_SECURITY") or "").strip().lower()
        if security not in {"starttls", "ssl", "none"}:
            security = "starttls"
        default_port = 465 if security == "ssl" else 25 if security == "none" else 587
        try:
            port = int(os.environ.get("MULTIUSER_SMTP_PORT", str(default_port)))
        except (TypeError, ValueError):
            port = default_port
        from_address = (
            os.environ.get("MULTIUSER_MAIL_FROM")
            or os.environ.get("MULTIUSER_SMTP_USERNAME")
            or ""
        ).strip()
        return cls(
            host=(os.environ.get("MULTIUSER_SMTP_HOST") or "").strip(),
            port=port,
            security=security,
            username=(os.environ.get("MULTIUSER_SMTP_USERNAME") or "").strip(),
            password=os.environ.get("MULTIUSER_SMTP_PASSWORD") or "",
            from_address=from_address,
            from_name=(os.environ.get("MULTIUSER_MAIL_FROM_NAME") or "Music Box").strip(),
            reply_to=(os.environ.get("MULTIUSER_MAIL_REPLY_TO") or "").strip(),
            timeout=int(os.environ.get("MULTIUSER_SMTP_TIMEOUT", "12") or 12),
            outbox_dir=(os.environ.get("MULTIUSER_MAIL_OUTBOX") or default_outbox_dir).strip(),
            redirect_to=(os.environ.get("MULTIUSER_MAIL_REDIRECT_TO") or "").strip(),
            suppress_send=_env_bool("MULTIUSER_MAIL_SUPPRESS_SEND"),
        )

    @property
    def configured(self) -> bool:
        return bool(self.host and self.from_address)

    def summary(self) -> dict:
        return {
            "configured": self.configured,
            "host": self.host,
            "port": self.port,
            "security": self.security,
            "from_address": self.from_address,
            "from_name": self.from_name,
            "reply_to": self.reply_to,
            "redirect_to": self.redirect_to,
            "outbox_dir": self.outbox_dir,
            "suppress_send": self.suppress_send,
        }


class Mailer:
    def __init__(self, config: MailerConfig, logger=None):
        self.config = config
        self.logger = logger
        self.last_result: Optional[MailResult] = None

    @classmethod
    def from_env(cls, default_outbox_dir: str = "", logger=None) -> "Mailer":
        return cls(MailerConfig.from_env(default_outbox_dir), logger=logger)

    @staticmethod
    def valid_address(address: str) -> bool:
        return bool(address and EMAIL_RE.match(address.strip()))

    def _build(
        self,
        to_address: str,
        subject: str,
        text: str,
        html: str,
        headers: Optional[dict] = None,
    ) -> EmailMessage:
        message = EmailMessage()
        message["Subject"] = subject[:180]
        message["From"] = formataddr((self.config.from_name, self.config.from_address))
        message["To"] = to_address
        if self.config.reply_to:
            message["Reply-To"] = self.config.reply_to
        message["Date"] = formatdate(localtime=False)
        message["Message-ID"] = make_msgid(domain=self.config.from_address.split("@")[-1] or None)
        message["Auto-Submitted"] = "auto-generated"
        message["X-Mailer"] = "MusicBox-MultiUser"
        for key, value in (headers or {}).items():
            if key and value:
                message[key] = str(value)[:900]
        message.set_content(text)
        message.add_alternative(html, subtype="html")
        return message

    def _write_outbox(self, message: EmailMessage, to_address: str) -> MailResult:
        directory = self.config.outbox_dir
        if not directory:
            return MailResult("disabled", "SMTP is not configured and no outbox directory is set.")
        try:
            os.makedirs(directory, exist_ok=True)
            try:
                os.chmod(directory, 0o700)
            except OSError:
                pass
            stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
            safe_to = re.sub(r"[^A-Za-z0-9._@-]", "_", to_address)[:80]
            path = os.path.join(directory, f"{stamp}-{uuid.uuid4().hex[:8]}-{safe_to}.eml")
            with open(path, "wb") as handle:
                handle.write(bytes(message))
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
            return MailResult("outbox", "SMTP is not configured; message stored in the local outbox.", path, message["Message-ID"])
        except OSError as exc:
            return MailResult("error", f"Could not write the outbox message: {exc}")

    def send(
        self,
        to_address: str,
        subject: str,
        text: str,
        html: str = "",
        headers: Optional[dict] = None,
    ) -> MailResult:
        to_address = (to_address or "").strip()
        if not self.valid_address(to_address):
            return MailResult("error", "Invalid recipient address.")
        if not html:
            html = (f'<!doctype html>\n<html>\n<body style="margin:0;padding:24px;'
                    f'background:{_INK};color:{_TEXT};font-family:{_SANS}">'
                    f'<pre style="white-space:pre-wrap;margin:0;font-size:13px;'
                    f'line-height:1.6;font-family:{_MONO}">{_escape(text)}</pre>'
                    f'</body>\n</html>')
        original_to = to_address
        if self.config.redirect_to:
            to_address = self.config.redirect_to
        message = self._build(to_address, subject, text, html, headers)
        if self.config.redirect_to and self.config.redirect_to != original_to:
            message["X-Original-To"] = original_to

        if self.config.suppress_send:
            result = MailResult("outbox", "Sending is suppressed (MULTIUSER_MAIL_SUPPRESS_SEND).", "", message["Message-ID"])
            self.last_result = result
            return result
        if not self.config.configured:
            result = self._write_outbox(message, to_address)
            self.last_result = result
            if self.logger and result.status == "outbox":
                self.logger.warning("[multiuser] SMTP not configured; email queued in outbox: %s", result.outbox_path)
            return result

        try:
            if self.config.security == "ssl":
                context = ssl.create_default_context()
                client = smtplib.SMTP_SSL(
                    self.config.host, self.config.port, timeout=self.config.timeout, context=context
                )
            else:
                client = smtplib.SMTP(self.config.host, self.config.port, timeout=self.config.timeout)
            with client:
                client.ehlo()
                if self.config.security == "starttls":
                    client.starttls(context=ssl.create_default_context())
                    client.ehlo()
                if self.config.username:
                    client.login(self.config.username, self.config.password)
                client.send_message(message)
            result = MailResult("sent", "Email sent.", "", message["Message-ID"])
            self.last_result = result
            if self.logger:
                self.logger.info("[multiuser] email sent to %s (subject=%r)", _mask(to_address), subject[:60])
            return result
        except (smtplib.SMTPException, OSError, ssl.SSLError) as exc:
            # Do not include the SMTP password or message body in the error.
            result = MailResult("error", f"SMTP delivery failed: {type(exc).__name__}: {exc}")
            self.last_result = result
            if self.logger:
                self.logger.error("[multiuser] email delivery failed to %s: %s", _mask(to_address), result.detail)
            return result


def _escape(value: str) -> str:
    return (
        (value or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


# The palette is the site's own, from templates/static/css/signin.css.  Mail is
# the one surface where the brand is seen outside the app, so it repeats the real
# tokens rather than a guess at them: #0a0a0a page, #141414 card, #252525 rules,
# #e8e8e8 text, #666 fine print, #e8590c accent.  Everything is square-cornered
# because the site is, and every value is repeated literally because Gmail drops
# <style> blocks and Outlook renders through Word.
_INK = "#0a0a0a"
_SURFACE = "#141414"
_RULE = "#252525"
_TEXT = "#e8e8e8"
_BODY = "#c7c7c7"
_FAINT = "#8a8a8a"
_MUTED = "#666666"
_ACCENT = "#e8590c"
_DANGER = "#e03131"
_SANS = "-apple-system,BlinkMacSystemFont,'Segoe UI',system-ui,Roboto,Helvetica,Arial,sans-serif"
_MONO = "ui-monospace,SFMono-Regular,Menlo,Consolas,monospace"


def _mask(address: str) -> str:
    try:
        local, domain = address.split("@", 1)
        return f"{local[:2]}***@{domain}"
    except ValueError:
        return "***"


def _mark() -> str:
    """The five-bar equaliser from the sign-in brand, without flex.

    ``display:flex`` does not exist in Outlook, so the bars are inline-block
    cells with the same 35/75/50/90/60% heights the stylesheet uses, aligned to
    a common bottom edge.
    """
    bars = (8, 17, 11, 20, 13)
    return "".join(
        f'<span style="display:inline-block;width:3px;height:{height}px;'
        f"background:{_ACCENT};vertical-align:bottom"
        f'{";margin-right:3px" if index < len(bars) - 1 else ""}"></span>'
        for index, height in enumerate(bars)
    )


def _label(text: str) -> str:
    return (f'<p style="margin:0 0 7px;font-size:10px;font-weight:600;'
            f"text-transform:uppercase;letter-spacing:.14em;color:{_MUTED};"
            f'font-family:{_SANS}">{_escape(text)}</p>')


def _field(value: str) -> str:
    return (f'      <p style="margin:0 0 16px;padding:11px 14px;background:{_INK};'
            f'border:1px solid {_RULE};font-size:13px;line-height:1.6;color:{_TEXT};'
            f'word-break:break-word">{_escape(value)}</p>')


def _note(text: str, rule: str = _ACCENT) -> str:
    """A caution line with a coloured left rule.

    The site never shouts at you in a coloured box; a 3px edge is how the web app
    marks a note worth reading -- accent for advice, danger for a fault.
    """
    return (f'      <p style="margin:0 0 14px;padding:11px 14px;border-left:3px solid {rule};'
            f'background:{_INK};font-size:12px;line-height:1.6;color:{_BODY}">{_escape(text)}</p>')


def _heading(heading: str, intro: str) -> str:
    return (f'      <h1 style="margin:0 0 8px;font-size:17px;font-weight:600;'
            f'letter-spacing:-.01em;color:{_TEXT}">{_escape(heading)}</h1>\n'
            f'      <p style="margin:0 0 20px;font-size:13.5px;line-height:1.6;'
            f'color:{_BODY}">{_escape(intro)}</p>')


def _fine_print(inner_html: str, break_all: bool = False) -> str:
    """The separated note at the bottom of a card.

    ``inner_html`` is expected to be escaped already, because the link emails put
    a coloured span inside it.
    """
    return (f'      <p style="margin:20px 0 0;padding-top:14px;border-top:1px solid {_RULE};'
            f'font-size:11px;line-height:1.6;color:{_MUTED}'
            f'{";word-break:break-all" if break_all else ""}">{inner_html}</p>')


def _code_box(code: str) -> str:
    """The code drawn as the site's own code field.

    ``.code-input`` in signin.css is a page-background box with a #252525 rule,
    centred monospaced digits and heavy letter-spacing, so a reader who has the
    mail open beside the browser sees the same shape they are about to type into.
    The right padding is smaller than the left because letter-spacing adds a
    space after the last digit, which would otherwise push the digits off centre.
    """
    return (f'      <div style="background:{_INK};border:1px solid {_RULE};padding:20px 8px 20px 20px;'
            f'text-align:center;font-family:{_MONO};font-size:28px;font-weight:600;'
            f'letter-spacing:.3em;color:#fff">{_escape(code)}</div>')


def _document(app_name: str, card_html: str, footer: str,
              trailing: str = "If you did not request this, you can ignore this email.") -> str:
    """The shell every transactional message is wrapped in.

    Inline styles are not laziness: Gmail strips ``<style>`` blocks and Outlook
    renders through Word, so a style attribute is the only thing that survives.
    ``trailing`` is a parameter because telling someone to ignore an alert about a
    server that is failing right now is the wrong advice.
    """
    return f"""\
<!doctype html>
<html>
<body style="margin:0;padding:0;background:{_INK};color:{_TEXT};font-family:{_SANS}">
  <div style="height:3px;background:{_ACCENT};font-size:3px;line-height:3px">&nbsp;</div>
  <div style="max-width:560px;margin:0 auto;padding:30px 20px 34px">
    <table role="presentation" cellpadding="0" cellspacing="0" style="border-collapse:collapse;margin:0 0 22px">
      <tr>
        <td style="vertical-align:bottom;padding-right:12px">{_mark()}</td>
        <td style="vertical-align:middle;font-size:13px;font-weight:700;\
letter-spacing:.14em;text-transform:uppercase;color:{_TEXT}">{_escape(app_name)}</td>
      </tr>
    </table>
    <div style="background:{_SURFACE};border:1px solid {_RULE};padding:26px 24px">
{card_html}
    </div>
    <p style="color:{_MUTED};font-size:11px;line-height:1.6;margin:16px 2px 0">{_escape(footer or '')}\
{("<br>" + _escape(trailing)) if trailing else ""}</p>
  </div>
</body>
</html>"""


def render_email(
    *,
    app_name: str,
    heading: str,
    intro: str,
    action_label: str,
    action_url: str,
    expiry: str,
    footer: str = "",
    warning: str = "",
) -> tuple[str, str]:
    """Return (text, html) for a message whose credential is a link to open."""
    text_lines = [
        f"{app_name}",
        "",
        heading,
        "",
        intro,
        "",
        f"{action_label}:",
        action_url,
        "",
        expiry,
    ]
    if warning:
        text_lines.extend(["", warning])
    if footer:
        text_lines.extend(["", footer])
    text_lines.extend(
        [
            "",
            "If you did not request this, you can ignore this email.",
            "",
            "— " + app_name,
        ]
    )
    card = "\n".join(
        [
            _heading(heading, intro),
            '      <p style="margin:0 0 18px">'
            f'<a href="{_escape(action_url)}" style="display:inline-block;background:{_ACCENT};'
            f'border:1px solid {_ACCENT};color:#fff;text-decoration:none;font-size:12px;'
            'font-weight:700;text-transform:uppercase;letter-spacing:.1em;padding:12px 20px">'
            f'{_escape(action_label)}</a></p>',
            f'      <p style="margin:0 0 14px;font-size:12px;color:{_FAINT}">{_escape(expiry)}</p>',
            (_note(warning) if warning else ""),
            _fine_print(
                f'{_escape(action_label)} does not work? Paste this address into your '
                f'browser:<br><span style="color:{_FAINT}">{_escape(action_url)}</span>',
                break_all=True,
            ),
        ]
    )
    return "\n".join(text_lines), _document(app_name, card, footer)


def render_alert_email(
    *,
    app_name: str,
    heading: str,
    intro: str,
    rows: tuple = (),
    note: str = "",
    footer: str = "",
    fine_print: str = "",
    trailing: str = "",
) -> tuple[str, str]:
    """Return (text, html) for a message with no credential in it.

    Alerts describe a server that needs a human rather than a credential to type,
    so there is no code box and no button -- just what happened, in the same
    label-over-value shape the site uses, and what to do about it.

    The two closing lines are the only part that is about the *reader* rather than
    the event, so they are arguments: an operator alert and a notice to an account
    holder are the same shape addressed to two people who need different reassurance.
    """
    text_lines = [app_name, "", heading, "", intro]
    for label, value in rows:
        if value:
            text_lines.extend(["", f"{label}: {value}"])
    if note:
        text_lines.extend(["", note])
    if footer:
        text_lines.extend(["", footer])
    body = [_heading(heading, intro)]
    for label, value in rows:
        if value:
            body.extend([_label(label), _field(value)])
    if note:
        body.append(_note(note, _DANGER))
    body.append(_fine_print(
        _escape(fine_print) or f"Sent automatically by {_escape(app_name)} because this needs someone to look."))
    text_lines.extend(["", "— " + app_name])
    return "\n".join(text_lines), _document(
        app_name, "\n".join(body), footer,
        trailing=trailing or "Nothing in this email needs a reply; it is here so you see it before a user does.",
    )


def render_code_email(
    *,
    app_name: str,
    heading: str,
    intro: str,
    code: str,
    expiry: str,
    where: str,
    footer: str = "",
    warning: str = "",
) -> tuple[str, str]:
    """Return (text, html) for a message whose credential is a code to type.

    No anchor, no URL, no "paste this link" line anywhere in the output -- that is
    the entire point of the switch.  A forwarded code email is inert until somebody
    types it into a page that also has to be reached by hand, whereas a forwarded
    link was one click from whomever it was forwarded to.  The digits stay
    contiguous so a person copying them does not copy a space.
    """
    text_lines = [
        app_name,
        "",
        heading,
        "",
        intro,
        "",
        f"Your code is {code}",
        "",
        f"Enter it on {where}.",
        "",
        expiry,
    ]
    if warning:
        text_lines.extend(["", warning])
    if footer:
        text_lines.extend(["", footer])
    text_lines.extend(["", "If you did not request this, you can ignore this email.",
                       "", "— " + app_name])
    card = "\n".join(
        [
            _heading(heading, intro),
            _label("Your code"),
            _code_box(code),
            f'      <p style="margin:14px 0 14px;font-size:13px;line-height:1.6;color:{_BODY}">'
            f'Enter it on <span style="color:{_TEXT}">{_escape(where)}</span>.</p>',
            f'      <p style="margin:0 0 14px;font-size:12px;color:{_FAINT}">{_escape(expiry)}</p>',
            (_note(warning) if warning else ""),
            _fine_print("This email has no button in it on purpose: nothing here "
                        "works without you."),
        ]
    )
    return "\n".join(text_lines), _document(app_name, card, footer)
