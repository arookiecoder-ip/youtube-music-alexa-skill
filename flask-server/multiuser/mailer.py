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
            html = f"<html><body><pre style='white-space:pre-wrap'>{_escape(text)}</pre></body></html>"
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


def _mask(address: str) -> str:
    try:
        local, domain = address.split("@", 1)
        return f"{local[:2]}***@{domain}"
    except ValueError:
        return "***"


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
    """Return (text, html) for a branded transactional email."""
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
            "— Music Box",
        ]
    )
    text = "\n".join(text_lines)
    html = f"""\
<!doctype html>
<html>
<body style="margin:0;padding:0;background:#050505;color:#f5f5f5;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif">
  <div style="max-width:560px;margin:0 auto;padding:32px 20px">
    <div style="display:flex;align-items:center;gap:10px;margin-bottom:22px">
      <span style="display:inline-grid;place-items:center;width:34px;height:34px;border-radius:10px;background:#e8590c;font-size:18px">♫</span>
      <span style="font-weight:700;font-size:16px">{_escape(app_name)}</span>
    </div>
    <div style="background:#151515;border:1px solid #2b2b2b;border-radius:14px;padding:28px">
      <h1 style="margin:0 0 10px;font-size:22px;letter-spacing:-.02em">{_escape(heading)}</h1>
      <p style="margin:0 0 20px;color:#b3b3b3;line-height:1.55">{_escape(intro)}</p>
      <p style="margin:0 0 22px">
        <a href="{_escape(action_url)}" style="display:inline-block;background:#e8590c;color:#fff;text-decoration:none;font-weight:600;padding:11px 20px;border-radius:9px">{_escape(action_label)}</a>
      </p>
      <p style="margin:0 0 12px;color:#8c8c8c;font-size:13px">{_escape(expiry)}</p>
      {f'<p style="margin:0 0 12px;color:#e3b341;font-size:13px">{_escape(warning)}</p>' if warning else ''}
      <p style="margin:18px 0 0;color:#666;font-size:12px;word-break:break-all">If the button does not work, paste this link into your browser:<br><span style="color:#9a9a9a">{_escape(action_url)}</span></p>
    </div>
    <p style="color:#666;font-size:12px;line-height:1.5;margin:18px 4px 0">{_escape(footer or '')}<br>If you did not request this, you can ignore this email.</p>
  </div>
</body>
</html>"""
    return text, html
