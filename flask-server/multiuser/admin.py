"""Feature-flagged admin panel and multi-user HTTP surface.

Mounted at ``/admin`` only when ``MULTI_USER_ENABLED=1``.  The existing web
remote and device APIs are not modified by this module.
"""
from __future__ import annotations

import csv
import io
import json
import os
import shutil
import time
from datetime import datetime, timezone
from functools import wraps
from urllib.parse import urlencode

from flask import (
    Blueprint,
    Response,
    current_app,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from .accounts import AccountError, AccountService, ROLES, STATUSES
from .config import MultiUserConfig
from .crypto import SecretBox
from .db import Database
from .mailer import Mailer, render_email
from .security import RateLimiter, generate_password

PAGE_SIZE = 25
AUDIT_PAGE_SIZE = 50


def _now() -> float:
    return time.time()


def _client_ip() -> str:
    return (
        request.headers.get("CF-Connecting-IP")
        or request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
        or request.remote_addr
        or ""
    )[:64]


def _user_agent() -> str:
    return (request.headers.get("User-Agent") or "")[:300]


def _safe_next(value: str) -> str:
    value = (value or "").strip()
    if value.startswith("/admin/") and not value.startswith("//"):
        return value
    return "/admin/"


def _query_url(**overrides) -> str:
    args = request.args.to_dict(flat=True)
    args.update({key: value for key, value in overrides.items() if value not in (None, "")})
    for key in [key for key, value in args.items() if value in (None, "")]:
        args.pop(key, None)
    query = urlencode(args)
    return f"{request.path}?{query}" if query else request.path


def _pagination(page: int, total: int, per_page: int) -> dict:
    pages = max(1, (total + per_page - 1) // per_page)
    page = min(max(1, page), pages)
    return {
        "page": page,
        "pages": pages,
        "per_page": per_page,
        "total": total,
        "has_prev": page > 1,
        "has_next": page < pages,
        "prev_url": _query_url(page=page - 1),
        "next_url": _query_url(page=page + 1),
        "start": (page - 1) * per_page + 1 if total else 0,
        "end": min(page * per_page, total),
    }


def _parse_page() -> int:
    try:
        return max(1, int(request.args.get("page", "1")))
    except (TypeError, ValueError):
        return 1


class MultiUser:
    """Owns the isolated services and the admin blueprint."""

    def __init__(self, config: MultiUserConfig, app_secret: str = "", logger=None):
        self.config = config
        self.logger = logger
        self.db = Database(config.db_path)
        self.secret_box = SecretBox(config.encryption_key, fallback_secret=app_secret)
        self.accounts = AccountService(self.db, config, self.secret_box)
        self.rate_limiter = RateLimiter()
        default_outbox = os.path.join(
            os.path.dirname(os.path.abspath(config.db_path)), "mail-outbox"
        )
        self.mailer = Mailer.from_env(default_outbox_dir=default_outbox, logger=logger)
        self.blueprint: Blueprint | None = None
        self.bootstrap_result: dict = {}
        self.initialized_at = 0.0

    # --------------------------------------------------------------- lifecycle
    def init_app(self, app) -> None:
        problems = self.config.validate()
        if problems:
            raise RuntimeError("; ".join(problems))
        self.bootstrap_result = self.accounts.bootstrap()
        self.initialized_at = _now()
        self.blueprint = build_blueprint(self)
        app.register_blueprint(self.blueprint)
        app.extensions["multiuser"] = self
        if self.bootstrap_result.get("created"):
            self._log(
                "Created bootstrap owner '%s'.%s",
                self.bootstrap_result.get("username"),
                (
                    f" Generated password written to {self.bootstrap_result.get('password_file')}"
                    if self.bootstrap_result.get("generated_password")
                    else " Password came from MULTIUSER_ADMIN_PASSWORD."
                ),
            )
        else:
            self._log("Multi-user layer ready (database=%s).", self.config.db_path)
        mail = self.mailer.config
        if mail.configured:
            self._log(
                "Email delivery configured via %s:%s (%s) as %s.",
                mail.host,
                mail.port,
                mail.security,
                mail.from_address,
            )
        else:
            self._log(
                "Email delivery is NOT configured; one-time links will be written to %s. "
                "Set MULTIUSER_SMTP_* and MULTIUSER_MAIL_FROM.",
                mail.outbox_dir,
            )

    def absolute_url(self, path: str, fallback_root: str = "") -> str:
        root = (self.config.public_base_url or fallback_root or "").rstrip("/")
        return f"{root}{path}" if root else path

    def _log(self, message: str, *args) -> None:
        if self.logger:
            self.logger.info("[multiuser] " + message, *args)


def build_blueprint(mu: MultiUser) -> Blueprint:
    bp = Blueprint(
        "multiuser_admin",
        __name__,
        url_prefix=mu.config.base_path,
        template_folder=os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates"),
    )
    base = mu.config.base_path.rstrip("/") or "/admin"
    login_url = f"{base}/login/"

    # ------------------------------------------------------------- CSRF/token
    def csrf_token() -> str:
        token = session.get("mu_csrf")
        if not token:
            import secrets

            token = secrets.token_urlsafe(32)
            session["mu_csrf"] = token
        return token

    def csrf_valid() -> bool:
        import hmac

        expected = session.get("mu_csrf") or ""
        supplied = request.form.get("_csrf") or request.headers.get("X-CSRF-Token") or ""
        return bool(expected) and hmac.compare_digest(str(expected), str(supplied))

    def rotate_csrf() -> None:
        import secrets

        session["mu_csrf"] = secrets.token_urlsafe(32)

    # --------------------------------------------------------------- identity
    def _load_identity() -> None:
        g.mu_user = None
        g.mu_session = None
        raw_sid = session.get("mu_sid")
        if not raw_sid:
            return
        mu_session, user = mu.accounts.validate_session(raw_sid)
        if not mu_session or not user:
            session.pop("mu_sid", None)
            session.pop("mu_user_id", None)
            return
        g.mu_session = mu_session
        g.mu_user = user

    def _wants_json() -> bool:
        return (
            request.path.startswith(f"{base}/api/")
            or request.accept_mimetypes.best == "application/json"
        )

    def _unauthorized():
        if _wants_json():
            return jsonify({"error": "admin_session_required"}), 401
        return redirect(f"{login_url}?{urlencode({'next': request.full_path[:-1] or base + '/'})}")

    def admin_required(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            if not g.get("mu_user"):
                return _unauthorized()
            if not AccountService.can_view_admin(g.mu_user):
                return _forbidden()
            return view(*args, **kwargs)

        return wrapper

    def _forbidden():
        if _wants_json():
            return jsonify({"error": "forbidden"}), 403
        return render_template("admin/error.html", status=403, message="You do not have permission to do that."), 403

    # -------------------------------------------------------------- lifecycle
    public_endpoints = {
        "multiuser_admin.health",
        "multiuser_admin.login",
        "multiuser_admin.forgot_password",
        "multiuser_admin.reset_password",
        "multiuser_admin.verify_email",
        "multiuser_admin.email_login",
        "multiuser_admin.magic_login",
        "multiuser_admin.accept_invite",
    }

    @bp.before_request
    def _guard():
        # Any admin POST needs a CSRF token, including login and public forms.
        if request.method in {"POST", "PUT", "PATCH", "DELETE"} and not csrf_valid():
            if _wants_json():
                return jsonify({"error": "csrf_failed"}), 400
            return (
                render_template(
                    "admin/error.html",
                    status=400,
                    message="Your form expired. Reload the page and try again.",
                ),
                400,
            )
        if request.endpoint in public_endpoints:
            return None
        _load_identity()
        if not g.get("mu_user"):
            return _unauthorized()
        if not AccountService.can_view_admin(g.mu_user):
            return _forbidden()
        # Coarse per-process request budget. Login has its own stricter limit.
        allowed, retry_after = mu.rate_limiter.allow(
            "admin-request", _client_ip(), mu.config.request_max_per_minute, 60
        )
        if not allowed:
            if _wants_json():
                return jsonify({"error": "rate_limited", "retry_after": retry_after}), 429
            return (
                render_template(
                    "admin/error.html",
                    status=429,
                    message=f"Too many requests. Try again in {retry_after}s.",
                ),
                429,
            )
        return None

    @bp.after_request
    def _security_headers(response):
        if request.path.startswith(base):
            response.headers["Cache-Control"] = "no-store"
            response.headers["Pragma"] = "no-cache"
            response.headers["X-Frame-Options"] = "DENY"
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Referrer-Policy"] = "same-origin"
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; style-src 'self' 'unsafe-inline'; "
                "script-src 'self' 'unsafe-inline'; img-src 'self' data:; "
                "form-action 'self'; frame-ancestors 'none'; base-uri 'self'"
            )
        return response

    @bp.context_processor
    def _context():
        return {
            "admin_user": g.get("mu_user"),
            "csrf_token": csrf_token(),
            "mu_config": mu.config,
            "base_path": base,
            "mail_configured": mu.mailer.config.configured,
            "magic_link_enabled": mu.config.magic_link_enabled,
            "current_year": datetime.now(timezone.utc).year,
        }

    @bp.app_template_filter("mu_datetime")
    def _filter_datetime(value):
        try:
            value = float(value)
        except (TypeError, ValueError):
            return "—"
        if value <= 0:
            return "—"
        return datetime.fromtimestamp(value, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    @bp.app_template_filter("mu_relative")
    def _filter_relative(value):
        try:
            value = float(value)
        except (TypeError, ValueError):
            return "—"
        if value <= 0:
            return "never"
        seconds = int(_now() - value)
        if seconds < 0:
            return "in the future"
        if seconds < 60:
            return f"{seconds}s ago"
        if seconds < 3600:
            return f"{seconds // 60}m ago"
        if seconds < 86400:
            return f"{seconds // 3600}h ago"
        return f"{seconds // 86400}d ago"

    @bp.app_template_filter("mu_json")
    def _filter_json(value):
        try:
            return json.dumps(value, indent=2, sort_keys=True)
        except (TypeError, ValueError):
            return str(value)

    # ----------------------------------------------------------- email helpers
    def _absolute(path: str) -> str:
        return mu.absolute_url(path, request.url_root)

    _email_copy = {
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

    def _token_path(purpose: str, token: str) -> str:
        routes = {
            "verify": f"{base}/verify-email/",
            "reset": f"{base}/reset-password/",
            "magic": f"{base}/magic/",
            "invite": f"{base}/invite/",
        }
        return routes[purpose] + token

    def _send_token_email(
        user: dict,
        purpose: str,
        token: str,
        *,
        actor: dict | None = None,
        ip: str = "",
        user_agent: str = "",
    ):
        heading, intro, label, expiry, warning = _email_copy[purpose]
        if purpose == "invite":
            expiry = expiry.format(hours=mu.config.email_token_invite_hours)
        elif purpose == "verify":
            expiry = expiry.format(minutes=mu.config.email_token_verify_hours * 60)
        elif purpose == "reset":
            expiry = expiry.format(minutes=mu.config.email_token_reset_minutes)
        else:
            expiry = expiry.format(minutes=mu.config.email_token_magic_minutes)
        url = _absolute(_token_path(purpose, token))
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
        action = {
            "verify": "verification_email",
            "reset": "password_reset_email",
            "magic": "magic_link_email",
            "invite": "invitation_email",
        }[purpose]
        mu.accounts.audit(
            actor,
            f"{action}_{'sent' if result.ok else 'failed'}",
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

    def _flash_mail(result, description: str, email: str = "") -> None:
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

    # ------------------------------------------------------------------ pages
    @bp.route("/", methods=["GET"])
    @admin_required
    def dashboard():
        counts = mu.accounts.counts()
        recent_audit, _ = mu.accounts.list_audit(per_page=12)
        recent_users, _ = mu.accounts.list_users(per_page=5)
        provider_rows = mu.db.fetchall(
            "SELECT provider, status, COUNT(*) AS count FROM user_provider_credentials "
            "GROUP BY provider, status"
        )
        providers: dict[str, dict[str, int]] = {}
        for row in provider_rows:
            providers.setdefault(row["provider"], {})[row["status"]] = row["count"]
        disk = shutil.disk_usage(os.path.dirname(os.path.abspath(mu.config.db_path)) or ".")
        schema = mu.db.schema_versions()
        return render_template(
            "admin/dashboard.html",
            page="dashboard",
            title="Dashboard",
            counts=counts,
            recent_audit=recent_audit,
            recent_users=recent_users,
            providers=providers,
            disk=disk,
            schema=schema,
            db_size=mu.db.file_size(),
            bootstrap=mu.bootstrap_result,
        )

    @bp.route("/users/", methods=["GET"])
    @admin_required
    def users():
        query = (request.args.get("q") or "").strip()[:120]
        status = (request.args.get("status") or "").strip()
        role = (request.args.get("role") or "").strip()
        page = _parse_page()
        rows, total = mu.accounts.list_users(query, status, role, page, PAGE_SIZE)
        pagination = _pagination(page, total, PAGE_SIZE)
        return render_template(
            "admin/users.html",
            page="users",
            title="Users",
            users=rows,
            total=total,
            pagination=pagination,
            q=query,
            status_filter=status,
            role_filter=role,
            roles=ROLES,
            statuses=STATUSES,
        )

    @bp.route("/users/new/", methods=["GET", "POST"])
    @admin_required
    def user_new():
        form = {
            "username": "",
            "display_name": "",
            "email": "",
            "role": "user",
            "password": "",
            "must_change_password": True,
            "send_invite": False,
            "send_verification": True,
        }
        if request.method == "POST":
            form = {
                "username": (request.form.get("username") or "").strip(),
                "display_name": (request.form.get("display_name") or "").strip(),
                "email": (request.form.get("email") or "").strip(),
                "role": (request.form.get("role") or "user").strip(),
                "password": request.form.get("password") or "",
                "must_change_password": request.form.get("must_change_password") == "on",
                "send_invite": request.form.get("send_invite") == "on",
                "send_verification": request.form.get("send_verification") == "on",
            }
            generated_password = False
            if form["send_invite"] and not form["email"]:
                flash("An email address is required to send an invitation.", "error")
            else:
                if not form["password"]:
                    form["password"] = generate_password()
                    generated_password = True
                try:
                    user = mu.accounts.create_user(
                        g.mu_user,
                        username=form["username"],
                        password=form["password"],
                        email=form["email"],
                        display_name=form["display_name"],
                        role=form["role"],
                        must_change_password=form["must_change_password"],
                        status="invited" if form["send_invite"] else "active",
                        email_verified=False,
                        ip=_client_ip(),
                        user_agent=_user_agent(),
                    )
                    flash(f"User '{user['username']}' created.", "success")
                    if form["send_invite"]:
                        token = mu.accounts.create_email_token(
                            user["id"], "invite", user["email"],
                            ip=_client_ip(), user_agent=_user_agent(),
                        )
                        result = _send_token_email(
                            user, "invite", token, actor=g.mu_user,
                            ip=_client_ip(), user_agent=_user_agent(),
                        )
                        _flash_mail(result, "Invitation email", user["email"])
                    elif form["send_verification"] and user.get("email"):
                        token = mu.accounts.create_email_token(
                            user["id"], "verify", user["email"],
                            ip=_client_ip(), user_agent=_user_agent(),
                        )
                        result = _send_token_email(
                            user, "verify", token, actor=g.mu_user,
                            ip=_client_ip(), user_agent=_user_agent(),
                        )
                        _flash_mail(result, "Verification email", user["email"])
                    if generated_password and not form["send_invite"]:
                        flash(f"Generated temporary password: {form['password']}", "password")
                    return redirect(url_for("multiuser_admin.user_detail", user_id=user["id"]))
                except AccountError as exc:
                    flash(str(exc), "error")
        return render_template(
            "admin/user_new.html",
            page="users",
            title="Create user",
            form=form,
            roles=ROLES,
            allowed_roles=[role for role in ROLES if AccountService.can_assign_role(g.mu_user, role)],
        )

    @bp.route("/users/<user_id>/", methods=["GET"])
    @admin_required
    def user_detail(user_id):
        user = mu.accounts.get_user(user_id)
        if not user:
            flash("User not found.", "error")
            return redirect(url_for("multiuser_admin.users"))
        if not AccountService.can_manage_user(g.mu_user, user) and g.mu_user["id"] != user_id:
            return _forbidden()
        return render_template(
            "admin/user_detail.html",
            page="users",
            title=user["username"],
            user=user,
            sessions=mu.accounts.list_sessions(user_id),
            providers=mu.accounts.provider_status(user_id),
            audit=mu.accounts.user_audit(user_id, limit=20),
            roles=ROLES,
            allowed_roles=[role for role in ROLES if AccountService.can_assign_role(g.mu_user, role)],
            can_manage=AccountService.can_manage_user(g.mu_user, user),
            is_self=g.mu_user["id"] == user_id,
        )

    @bp.route("/users/<user_id>/edit/", methods=["POST"])
    @admin_required
    def user_edit(user_id):
        if user_id == g.mu_user["id"]:
            flash("Use My account to update your own profile.", "info")
            return redirect(url_for("multiuser_admin.account"))
        try:
            mu.accounts.update_user(
                g.mu_user,
                user_id,
                {
                    "username": request.form.get("username"),
                    "display_name": request.form.get("display_name"),
                    "email": request.form.get("email"),
                    "role": request.form.get("role"),
                    "status": request.form.get("status"),
                },
                ip=_client_ip(),
                user_agent=_user_agent(),
            )
            flash("User updated.", "success")
        except AccountError as exc:
            flash(str(exc), "error")
        return redirect(url_for("multiuser_admin.user_detail", user_id=user_id))

    @bp.route("/users/<user_id>/password/", methods=["POST"])
    @admin_required
    def user_password(user_id):
        if user_id == g.mu_user["id"]:
            flash("Use My account to change your own password.", "info")
            return redirect(url_for("multiuser_admin.account"))
        password = request.form.get("password") or ""
        try:
            _, password, generated = mu.accounts.reset_password(
                g.mu_user,
                user_id,
                password or None,
                ip=_client_ip(),
                user_agent=_user_agent(),
            )
            label = "Generated password" if generated else "Password reset"
            flash(f"{label}: {password}", "password")
            flash("All sessions for this user were signed out.", "info")
        except AccountError as exc:
            flash(str(exc), "error")
        return redirect(url_for("multiuser_admin.user_detail", user_id=user_id))

    @bp.route("/users/<user_id>/status/", methods=["POST"])
    @admin_required
    def user_status(user_id):
        status = (request.form.get("status") or "").strip()
        try:
            mu.accounts.set_status(g.mu_user, user_id, status, ip=_client_ip(), user_agent=_user_agent())
            flash(f"User status set to {status}.", "success")
        except AccountError as exc:
            flash(str(exc), "error")
        return redirect(url_for("multiuser_admin.user_detail", user_id=user_id))

    @bp.route("/users/<user_id>/totp/", methods=["POST"])
    @admin_required
    def user_totp(user_id):
        try:
            mu.accounts.reset_totp(g.mu_user, user_id, ip=_client_ip(), user_agent=_user_agent())
            flash("Two-factor authentication was reset for this user.", "success")
        except AccountError as exc:
            flash(str(exc), "error")
        return redirect(url_for("multiuser_admin.user_detail", user_id=user_id))

    @bp.route("/users/<user_id>/email/verification/", methods=["POST"])
    @admin_required
    def user_email_verification(user_id):
        user = mu.accounts.get_user(user_id)
        if not user:
            flash("User not found.", "error")
            return redirect(url_for("multiuser_admin.users"))
        if not AccountService.can_manage_user(g.mu_user, user):
            return _forbidden()
        if not user.get("email"):
            flash("Add an email address first.", "error")
            return redirect(url_for("multiuser_admin.user_detail", user_id=user_id))
        if user.get("email_verified"):
            flash("That email address is already verified.", "info")
            return redirect(url_for("multiuser_admin.user_detail", user_id=user_id))
        if mu.accounts.recent_email_token_count(user_id, "verify", 60):
            flash("A verification email was sent less than a minute ago. Check the inbox first.", "warning")
            return redirect(url_for("multiuser_admin.user_detail", user_id=user_id))
        token = mu.accounts.create_email_token(
            user_id, "verify", user["email"], ip=_client_ip(), user_agent=_user_agent()
        )
        result = _send_token_email(
            user, "verify", token, actor=g.mu_user, ip=_client_ip(), user_agent=_user_agent()
        )
        _flash_mail(result, "Verification email", user["email"])
        return redirect(url_for("multiuser_admin.user_detail", user_id=user_id))

    @bp.route("/users/<user_id>/email/verify-now/", methods=["POST"])
    @admin_required
    def user_email_verify_now(user_id):
        try:
            mu.accounts.mark_email_verified(
                g.mu_user, user_id, ip=_client_ip(), user_agent=_user_agent(), method="admin_override"
            )
            flash("Email marked as verified.", "success")
        except AccountError as exc:
            flash(str(exc), "error")
        return redirect(url_for("multiuser_admin.user_detail", user_id=user_id))

    @bp.route("/users/<user_id>/email/invitation/", methods=["POST"])
    @admin_required
    def user_email_invitation(user_id):
        user = mu.accounts.get_user(user_id)
        if not user:
            flash("User not found.", "error")
            return redirect(url_for("multiuser_admin.users"))
        if not AccountService.can_manage_user(g.mu_user, user):
            return _forbidden()
        if user.get("status") != "invited":
            flash("Only invited accounts can receive an activation link.", "info")
            return redirect(url_for("multiuser_admin.user_detail", user_id=user_id))
        if not user.get("email"):
            flash("Add an email address first.", "error")
            return redirect(url_for("multiuser_admin.user_detail", user_id=user_id))
        token = mu.accounts.create_email_token(
            user_id, "invite", user["email"], ip=_client_ip(), user_agent=_user_agent()
        )
        result = _send_token_email(
            user, "invite", token, actor=g.mu_user, ip=_client_ip(), user_agent=_user_agent()
        )
        _flash_mail(result, "Invitation email", user["email"])
        return redirect(url_for("multiuser_admin.user_detail", user_id=user_id))

    @bp.route("/system/test-email/", methods=["POST"])
    @admin_required
    def system_test_email():
        if g.mu_user["role"] != "owner":
            flash("Only an owner can send a test email.", "error")
            return redirect(url_for("multiuser_admin.system"))
        recipient = (request.form.get("email") or g.mu_user.get("email") or "").strip()
        if not Mailer.valid_address(recipient):
            flash("Enter a valid recipient address.", "error")
            return redirect(url_for("multiuser_admin.system"))
        text, html = render_email(
            app_name=mu.config.app_name,
            heading="SMTP test successful",
            intro="This is a test message from the Music Box multi-user admin panel.",
            action_label="Open the admin panel",
            action_url=_absolute(f"{base}/"),
            expiry="This message does not expire.",
            footer=f"SMTP host: {mu.mailer.config.host or 'not configured'}",
        )
        result = mu.mailer.send(recipient, f"{mu.config.app_name}: SMTP test", text, html)
        mu.accounts.audit(
            g.mu_user, "test_email_sent" if result.ok else "test_email_failed", None,
            {"delivery": result.status, "error": (result.detail or "")[:200] if not result.ok else ""},
            _client_ip(), _user_agent(),
        )
        _flash_mail(result, "Test email", recipient)
        return redirect(url_for("multiuser_admin.system"))

    @bp.route("/users/<user_id>/sessions/revoke/", methods=["POST"])
    @admin_required
    def user_sessions_revoke(user_id):
        try:
            count = mu.accounts.revoke_all_sessions(
                g.mu_user, user_id, ip=_client_ip(), user_agent=_user_agent()
            )
            flash(f"Revoked {count} session(s).", "success")
        except AccountError as exc:
            flash(str(exc), "error")
        return redirect(url_for("multiuser_admin.user_detail", user_id=user_id))

    @bp.route("/users/<user_id>/provider/<provider>/disconnect/", methods=["POST"])
    @admin_required
    def user_provider_disconnect(user_id, provider):
        try:
            mu.accounts.clear_provider_credentials(
                g.mu_user, user_id, provider, ip=_client_ip(), user_agent=_user_agent()
            )
            flash(f"{provider.title()} credentials disconnected.", "success")
        except AccountError as exc:
            flash(str(exc), "error")
        return redirect(url_for("multiuser_admin.user_detail", user_id=user_id))

    @bp.route("/users/<user_id>/delete/", methods=["POST"])
    @admin_required
    def user_delete(user_id):
        try:
            mu.accounts.delete_user(g.mu_user, user_id, ip=_client_ip(), user_agent=_user_agent())
            flash("User deleted. Provider credentials were removed.", "success")
            return redirect(url_for("multiuser_admin.users"))
        except AccountError as exc:
            flash(str(exc), "error")
            return redirect(url_for("multiuser_admin.user_detail", user_id=user_id))

    @bp.route("/users/<user_id>/restore/", methods=["POST"])
    @admin_required
    def user_restore(user_id):
        try:
            mu.accounts.restore_user(g.mu_user, user_id, ip=_client_ip(), user_agent=_user_agent())
            flash("User restored in disabled state. Set a password before enabling.", "success")
        except AccountError as exc:
            flash(str(exc), "error")
        return redirect(url_for("multiuser_admin.users"))

    @bp.route("/audit/", methods=["GET"])
    @admin_required
    def audit():
        page = _parse_page()
        action = (request.args.get("action") or "").strip()
        actor = (request.args.get("actor") or "").strip()
        target = (request.args.get("target") or "").strip()
        rows, total = mu.accounts.list_audit(page, AUDIT_PAGE_SIZE, action, actor, target)
        return render_template(
            "admin/audit.html",
            page="audit",
            title="Audit log",
            events=rows,
            total=total,
            pagination=_pagination(page, total, AUDIT_PAGE_SIZE),
            action=action,
            actor=actor,
            target=target,
            actions=mu.accounts.audit_actions(),
        )

    @bp.route("/audit/export.csv", methods=["GET"])
    @admin_required
    def audit_export():
        rows, _ = mu.accounts.list_audit(1, 5000)
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(["timestamp_utc", "actor", "action", "target_user_id", "ip", "details"])
        for event in rows:
            writer.writerow(
                [
                    datetime.fromtimestamp(event["created_at"], tz=timezone.utc).isoformat(),
                    event.get("actor_label", ""),
                    event.get("action", ""),
                    event.get("target_user_id") or "",
                    event.get("ip", ""),
                    json.dumps(event.get("details", {}), separators=(",", ":")),
                ]
            )
        mu.accounts.audit(g.mu_user, "audit_exported", None, {"rows": len(rows)}, _client_ip(), _user_agent())
        return Response(
            buffer.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment; filename=musicbox-audit.csv"},
        )

    @bp.route("/system/", methods=["GET"])
    @admin_required
    def system():
        db_dir = os.path.dirname(os.path.abspath(mu.config.db_path)) or "."
        disk = shutil.disk_usage(db_dir)
        backups = []
        if os.path.isdir(mu.config.backup_dir):
            for name in sorted(os.listdir(mu.config.backup_dir), reverse=True)[:20]:
                path = os.path.join(mu.config.backup_dir, name)
                if os.path.isfile(path):
                    backups.append({"name": name, "size": os.path.getsize(path), "mtime": os.path.getmtime(path)})
        outbox_count = 0
        if os.path.isdir(mu.mailer.config.outbox_dir):
            try:
                outbox_count = sum(
                    1 for name in os.listdir(mu.mailer.config.outbox_dir) if name.endswith(".eml")
                )
            except OSError:
                outbox_count = 0
        return render_template(
            "admin/system.html",
            page="system",
            title="System",
            db_size=mu.db.file_size(),
            db_integrity=mu.db.integrity_check(),
            schema=mu.db.schema_versions(),
            disk=disk,
            backups=backups,
            backup_dir=mu.config.backup_dir,
            bootstrap=mu.bootstrap_result,
            initialized_at=mu.initialized_at,
            mail=mu.mailer.config.summary(),
            mail_last=mu.mailer.last_result,
            outbox_count=outbox_count,
        )

    @bp.route("/system/backup/", methods=["POST"])
    @admin_required
    def system_backup():
        if g.mu_user["role"] != "owner":
            flash("Only an owner can create backups.", "error")
            return redirect(url_for("multiuser_admin.system"))
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        destination = os.path.join(mu.config.backup_dir, f"data-{stamp}.db")
        try:
            mu.db.backup_to(destination)
            mu.accounts.audit(
                g.mu_user, "backup_created", None,
                {"file": os.path.basename(destination)}, _client_ip(), _user_agent(),
            )
            flash(f"Backup created: {destination}", "success")
        except Exception as exc:  # noqa: BLE001 - show a safe operational message
            current_app.logger.exception("multi-user backup failed")
            flash(f"Backup failed: {exc}", "error")
        return redirect(url_for("multiuser_admin.system"))

    @bp.route("/account/", methods=["GET", "POST"])
    @admin_required
    def account():
        if request.method == "POST":
            current = request.form.get("current_password") or ""
            new = request.form.get("new_password") or ""
            confirm = request.form.get("confirm_password") or ""
            if new != confirm:
                flash("New password and confirmation do not match.", "error")
            else:
                try:
                    mu.accounts.change_own_password(
                        g.mu_user, current, new, ip=_client_ip(), user_agent=_user_agent()
                    )
                    flash("Password changed.", "success")
                    return redirect(url_for("multiuser_admin.account"))
                except AccountError as exc:
                    flash(str(exc), "error")
        return render_template(
            "admin/account.html",
            page="account",
            title="My account",
            user=mu.accounts.get_user(g.mu_user["id"]),
            sessions=mu.accounts.list_sessions(g.mu_user["id"]),
            audit=mu.accounts.user_audit(g.mu_user["id"], limit=15),
        )

    @bp.route("/account/profile/", methods=["POST"])
    @admin_required
    def account_profile():
        previous_email = (g.mu_user.get("email") or "").strip().lower()
        try:
            user = mu.accounts.update_own_profile(
                g.mu_user,
                display_name=request.form.get("display_name") or "",
                email=request.form.get("email") or "",
                current_password=request.form.get("current_password") or "",
                ip=_client_ip(),
                user_agent=_user_agent(),
            )
            flash("Profile updated.", "success")
            if (
                user.get("email")
                and user["email"].strip().lower() != previous_email
                and not user.get("email_verified")
            ):
                token = mu.accounts.create_email_token(
                    user["id"], "verify", user["email"],
                    ip=_client_ip(), user_agent=_user_agent(),
                )
                result = _send_token_email(
                    user, "verify", token, actor=g.mu_user,
                    ip=_client_ip(), user_agent=_user_agent(),
                )
                _flash_mail(result, "Verification email", user["email"])
        except AccountError as exc:
            flash(str(exc), "error")
        return redirect(url_for("multiuser_admin.account"))

    @bp.route("/account/email/verification/", methods=["POST"])
    @admin_required
    def account_email_verification():
        user = mu.accounts.get_user(g.mu_user["id"])
        if not user:
            flash("User not found.", "error")
            return redirect(url_for("multiuser_admin.account"))
        if not user.get("email"):
            flash("Add an email address first.", "error")
            return redirect(url_for("multiuser_admin.account"))
        if user.get("email_verified"):
            flash("Your email address is already verified.", "info")
            return redirect(url_for("multiuser_admin.account"))
        if mu.accounts.recent_email_token_count(user["id"], "verify", 60):
            flash("A verification email was sent less than a minute ago. Check your inbox.", "warning")
            return redirect(url_for("multiuser_admin.account"))
        token = mu.accounts.create_email_token(
            user["id"], "verify", user["email"], ip=_client_ip(), user_agent=_user_agent()
        )
        result = _send_token_email(
            user, "verify", token, actor=g.mu_user, ip=_client_ip(), user_agent=_user_agent()
        )
        _flash_mail(result, "Verification email", user["email"])
        return redirect(url_for("multiuser_admin.account"))

    @bp.route("/account/sessions/revoke/", methods=["POST"])
    @admin_required
    def account_sessions_revoke():
        try:
            count = mu.accounts.revoke_all_sessions(
                g.mu_user, g.mu_user["id"], ip=_client_ip(), user_agent=_user_agent()
            )
            session.pop("mu_sid", None)
            session.pop("mu_user_id", None)
            flash(f"Signed out {count} session(s), including this one.", "success")
        except AccountError as exc:
            flash(str(exc), "error")
            return redirect(url_for("multiuser_admin.account"))
        return redirect(login_url)

    # ------------------------------------------------------------------ auth
    @bp.route("/login/", methods=["GET", "POST"])
    def login():
        if request.method == "GET":
            raw_sid = session.get("mu_sid")
            if raw_sid:
                _, user = mu.accounts.validate_session(raw_sid)
                if user and AccountService.can_view_admin(user):
                    return redirect(f"{base}/")
            return render_template(
                "admin/login.html",
                title="Admin sign in",
                next_target=_safe_next(request.args.get("next", "")),
            )

        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        next_target = _safe_next(request.form.get("next", ""))
        ip = _client_ip()
        # Two independent budgets: per account and per source address.
        account_allowed, account_retry = mu.rate_limiter.allow(
            "login-account", username.lower(), mu.config.login_max_attempts, mu.config.login_window_seconds
        )
        ip_allowed, ip_retry = mu.rate_limiter.allow(
            "login-ip", ip, mu.config.login_max_attempts * 10, mu.config.login_window_seconds
        )
        if not account_allowed or not ip_allowed:
            retry = max(account_retry, ip_retry, 1)
            mu.accounts.audit(
                None, username or "unknown", "login_rate_limited", None,
                {"retry_after": retry}, ip, _user_agent(),
            )
            flash(f"Too many sign-in attempts. Try again in {retry}s.", "error")
            return render_template("admin/login.html", title="Admin sign in", next_target=next_target), 429

        user, error = mu.accounts.authenticate(username, password, ip, _user_agent())
        if not user:
            flash(error, "error")
            return render_template("admin/login.html", title="Admin sign in", next_target=next_target), 401
        if not AccountService.can_view_admin(user):
            mu.accounts.audit(
                user, "admin_login_denied", user["id"], {"reason": "role"}, ip, _user_agent()
            )
            flash("This account does not have admin access.", "error")
            return render_template("admin/login.html", title="Admin sign in", next_target=next_target), 403

        # Successful authentication clears the per-account and per-IP budgets:
        # they exist to slow guessing, not to cap legitimate sign-ins.
        mu.rate_limiter.reset("login-account", username.lower())
        mu.rate_limiter.reset("login-ip", ip)
        raw_sid = mu.accounts.create_session(user, ip, _user_agent())
        session["mu_sid"] = raw_sid
        session["mu_user_id"] = user["id"]
        session.permanent = True
        rotate_csrf()
        mu.accounts.audit(user, "admin_login", user["id"], {}, ip, _user_agent())
        return redirect(next_target)

    # ------------------------------------------------------- email self-service
    @bp.route("/forgot-password/", methods=["GET", "POST"])
    def forgot_password():
        if request.method == "GET":
            return render_template(
                "admin/forgot_password.html",
                title="Reset password",
                next_target="",
            )
        identifier = (request.form.get("identifier") or "").strip()
        ip = _client_ip()
        allowed, retry = mu.rate_limiter.allow("forgot-ip", ip, 10, mu.config.login_window_seconds)
        id_allowed, id_retry = mu.rate_limiter.allow(
            "forgot-id", identifier.lower(), 3, mu.config.login_window_seconds
        )
        if not allowed or not id_allowed:
            flash(f"Too many requests. Try again in {max(retry, id_retry, 1)}s.", "error")
            return render_template("admin/forgot_password.html", title="Reset password"), 429
        user = (
            mu.accounts.find_user_by_email(identifier)
            if "@" in identifier
            else mu.accounts.get_user_by_username(identifier)
        )
        if (
            user
            and user.get("status") == "active"
            and user.get("email")
            and mu.accounts.recent_email_token_count(user["id"], "reset", 60) == 0
        ):
            token = mu.accounts.create_email_token(
                user["id"], "reset", user["email"], ip=ip, user_agent=_user_agent()
            )
            result = _send_token_email(user, "reset", token, ip=ip, user_agent=_user_agent())
            mu.accounts.audit(
                None, "password_reset_requested", user["id"],
                {"delivery": result.status}, ip, _user_agent(),
            )
        elif identifier:
            mu.accounts.audit(
                None, "password_reset_requested_unknown", None,
                {"identifier": identifier[:120]}, ip, _user_agent(),
            )
        # Enumeration-safe response regardless of whether the account exists.
        flash(
            "If an account matches that address, a password reset link has been sent. "
            "The link expires in one hour.",
            "success",
        )
        return redirect(login_url)

    @bp.route("/reset-password/<token>", methods=["GET", "POST"])
    def reset_password(token):
        info = mu.accounts.peek_email_token(token, "reset")
        if not info:
            return (
                render_template(
                    "admin/email_result.html",
                    title="Reset link expired",
                    heading="This reset link is no longer valid",
                    message="Reset links expire after one hour and can only be used once. Request a new one below.",
                    action_url=f"{base}/forgot-password/",
                    action_label="Request a new link",
                ),
                410,
            )
        if request.method == "GET":
            return render_template(
                "admin/reset_password.html",
                title="Choose a new password",
                token=token,
                email=info["email"],
            )
        password = request.form.get("password") or ""
        confirm = request.form.get("confirm_password") or ""
        if password != confirm:
            flash("Password and confirmation do not match.", "error")
            return render_template(
                "admin/reset_password.html", title="Choose a new password", token=token, email=info["email"]
            ), 400
        consumed = mu.accounts.consume_email_token(token, "reset")
        if not consumed:
            flash("This reset link has already been used or expired.", "error")
            return redirect(f"{base}/forgot-password/")
        try:
            mu.accounts.reset_password_with_token(
                consumed["user"]["id"], password, ip=_client_ip(), user_agent=_user_agent()
            )
        except AccountError as exc:
            flash(str(exc), "error")
            return render_template(
                "admin/reset_password.html", title="Choose a new password", token=token, email=info["email"]
            ), 400
        flash("Password reset. Sign in with your new password.", "success")
        return redirect(login_url)

    @bp.route("/verify-email/<token>", methods=["GET"])
    def verify_email(token):
        consumed = mu.accounts.consume_email_token(token, "verify")
        if not consumed:
            return (
                render_template(
                    "admin/email_result.html",
                    title="Verification link expired",
                    heading="This verification link is no longer valid",
                    message="Verification links expire after 24 hours and can only be used once. Ask an administrator to resend it.",
                    action_url=login_url,
                    action_label="Go to sign in",
                ),
                410,
            )
        mu.accounts.verify_email_with_token(
            consumed["user"]["id"], ip=_client_ip(), user_agent=_user_agent()
        )
        return render_template(
            "admin/email_result.html",
            title="Email verified",
            heading="Email verified",
            message=f"{consumed['email']} is now confirmed for @{consumed['user']['username']}. You can sign in.",
            action_url=login_url,
            action_label="Sign in",
        )

    @bp.route("/email-login/", methods=["GET", "POST"])
    def email_login():
        if not mu.config.magic_link_enabled:
            flash("Email sign-in links are disabled.", "error")
            return redirect(login_url)
        if request.method == "GET":
            return render_template("admin/email_login.html", title="Email sign-in link")
        identifier = (request.form.get("identifier") or "").strip()
        ip = _client_ip()
        allowed, retry = mu.rate_limiter.allow("magic-ip", ip, 10, mu.config.login_window_seconds)
        id_allowed, id_retry = mu.rate_limiter.allow(
            "magic-id", identifier.lower(), 3, mu.config.login_window_seconds
        )
        if not allowed or not id_allowed:
            flash(f"Too many requests. Try again in {max(retry, id_retry, 1)}s.", "error")
            return render_template("admin/email_login.html", title="Email sign-in link"), 429
        user = (
            mu.accounts.find_user_by_email(identifier)
            if "@" in identifier
            else mu.accounts.get_user_by_username(identifier)
        )
        if (
            user
            and user.get("status") == "active"
            and user.get("email")
            and AccountService.can_view_admin(user)
            and mu.accounts.recent_email_token_count(user["id"], "magic", 60) == 0
        ):
            token = mu.accounts.create_email_token(
                user["id"], "magic", user["email"], ip=ip, user_agent=_user_agent()
            )
            _send_token_email(user, "magic", token, ip=ip, user_agent=_user_agent())
        elif identifier:
            mu.accounts.audit(
                None, "magic_link_requested_unknown", None,
                {"identifier": identifier[:120]}, ip, _user_agent(),
            )
        flash(
            "If an administrator account matches that address, a one-time sign-in link has been sent. "
            "It expires in 15 minutes.",
            "success",
        )
        return redirect(login_url)

    @bp.route("/magic/<token>", methods=["GET"])
    def magic_login(token):
        consumed = mu.accounts.consume_email_token(token, "magic")
        if not consumed:
            return (
                render_template(
                    "admin/email_result.html",
                    title="Sign-in link expired",
                    heading="This sign-in link is no longer valid",
                    message="Sign-in links expire after 15 minutes and can only be used once. Request a new one below.",
                    action_url=f"{base}/email-login/",
                    action_label="Request a new link",
                ),
                410,
            )
        user = consumed["user"]
        if user.get("status") != "active" or not AccountService.can_view_admin(user):
            mu.accounts.audit(
                user, "magic_login_denied", user.get("id"), {"reason": "role_or_status"},
                _client_ip(), _user_agent(),
            )
            return (
                render_template(
                    "admin/email_result.html",
                    title="Sign-in denied",
                    heading="This account cannot access the admin panel",
                    message="The account is disabled or does not have an administrator role.",
                    action_url=login_url,
                    action_label="Back to sign in",
                ),
                403,
            )
        if not user.get("email_verified"):
            mu.accounts.verify_email_with_token(user["id"], ip=_client_ip(), user_agent=_user_agent())
        # Retire any session already active in this browser before replacing it.
        previous = session.get("mu_sid")
        if previous:
            previous_session, _ = mu.accounts.validate_session(previous)
            if previous_session:
                mu.accounts.revoke_session(previous_session["sid_hash"])
        raw_sid = mu.accounts.create_session(user, _client_ip(), _user_agent())
        session["mu_sid"] = raw_sid
        session["mu_user_id"] = user["id"]
        session.permanent = True
        rotate_csrf()
        mu.accounts.audit(user, "magic_login", user["id"], {}, _client_ip(), _user_agent())
        flash("Signed in with a one-time email link.", "success")
        return redirect(f"{base}/")

    @bp.route("/invite/<token>", methods=["GET", "POST"])
    def accept_invite(token):
        info = mu.accounts.peek_email_token(token, "invite")
        if not info:
            return (
                render_template(
                    "admin/email_result.html",
                    title="Invitation expired",
                    heading="This invitation is no longer valid",
                    message="Invitations expire after 72 hours and can only be used once. Ask an administrator for a new one.",
                    action_url=login_url,
                    action_label="Go to sign in",
                ),
                410,
            )
        if request.method == "GET":
            return render_template(
                "admin/accept_invite.html",
                title="Activate your account",
                token=token,
                email=info["email"],
                username=info["username"],
            )
        password = request.form.get("password") or ""
        confirm = request.form.get("confirm_password") or ""
        if password != confirm:
            flash("Password and confirmation do not match.", "error")
            return render_template(
                "admin/accept_invite.html", title="Activate your account", token=token,
                email=info["email"], username=info["username"],
            ), 400
        consumed = mu.accounts.consume_email_token(token, "invite")
        if not consumed:
            flash("This invitation has already been used or expired.", "error")
            return redirect(login_url)
        try:
            mu.accounts.complete_invitation(
                consumed["user"]["id"], password, ip=_client_ip(), user_agent=_user_agent()
            )
        except AccountError as exc:
            flash(str(exc), "error")
            return render_template(
                "admin/accept_invite.html", title="Activate your account", token=token,
                email=info["email"], username=info["username"],
            ), 400
        flash("Account activated. Sign in with your new password.", "success")
        return redirect(login_url)

    @bp.route("/logout/", methods=["POST"])
    def logout():
        _load_identity()
        if g.get("mu_session"):
            mu.accounts.revoke_session(g.mu_session["sid_hash"])
            if g.get("mu_user"):
                mu.accounts.audit(
                    g.mu_user, "admin_logout", g.mu_user["id"], {}, _client_ip(), _user_agent()
                )
        session.pop("mu_sid", None)
        session.pop("mu_user_id", None)
        session.pop("mu_csrf", None)
        return redirect(login_url)

    # ------------------------------------------------------------------- api
    @bp.route("/api/health/", methods=["GET"])
    def health():
        return jsonify(
            {
                "ok": True,
                "service": "musicbox-multiuser",
                "phase": 1,
                "time": _now(),
            }
        )

    @bp.route("/api/users/", methods=["GET"])
    @admin_required
    def api_users():
        page = _parse_page()
        rows, total = mu.accounts.list_users(
            (request.args.get("q") or "").strip(),
            (request.args.get("status") or "").strip(),
            (request.args.get("role") or "").strip(),
            page,
            min(100, int(request.args.get("per_page", "25") or 25)),
        )
        return jsonify({"users": rows, "total": total, "page": page})

    return bp
