"""Feature-flagged admin panel and multi-user HTTP surface.

Mounted at ``/admin`` only when ``MULTI_USER_ENABLED=1``.  The existing web
remote and device APIs are not modified by this module.
"""
from __future__ import annotations

from auth_sessions import replace_identity, password_change_allowed, complete_login

import csv
import io
import json
import os
import shutil
import time
from datetime import datetime, timedelta, timezone
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

from . import devicename, emails, netids, pending
from .accounts import AccountError, AccountService, ROLES, SIGNUP_SWITCH_KEY, STATUSES
from .config import MultiUserConfig
from .crypto import SecretBox
from .db import Database
from .mailer import Mailer, render_email
from .security import RateLimiter, generate_password, validate_password
from .selfservice import build_selfservice_blueprint

PAGE_SIZE = 25
AUDIT_PAGE_SIZE = 50
# The panel theme is a preference, not a session value, so it survives a restart
# and follows the account between browsers.
THEME_KEY = "admin_theme"


def _now() -> float:
    return time.time()


def _client_ip() -> str:
    # The forwarded headers a visitor controls are never believed on their own;
    # see netids, whose rightmost-XFF rule is the only one that survives Caddy
    # appending rather than replacing what the client sent.
    return netids.client_ip(request)


def _user_agent() -> str:
    return netids.user_agent(request)


def _theme_for(layer, user: dict | None) -> str:
    """The panel theme this administrator last chose.

    Read per render rather than kept in a session, so the choice follows the
    account to another browser instead of being a secret one tab happens to hold.
    An unreadable preference is not worth a 500: the dark theme is what every
    other surface already shows.
    """
    if not user:
        return "dark"
    try:
        stored = layer.accounts.get_preference(user["id"], THEME_KEY)
    except Exception:
        return "dark"
    return stored if stored in ("dark", "light") else "dark"


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


def _dashboard_analytics(mu: "MultiUser") -> dict:
    """Fourteen daily sign-up bars plus the role, status and provider mixes.

    Deleted accounts are left out of the mixes because every list hides them,
    but they stay in the daily bars: a sign-up that was later deleted still
    happened. Percentages are of live accounts.
    """
    today = datetime.now(timezone.utc).date()
    first = today - timedelta(days=13)
    days = []
    for offset in range(13, -1, -1):
        day = today - timedelta(days=offset)
        days.append({
            "key": day.isoformat(),
            "label": f"{day.day:02d}",
            "title": day.strftime("%a %d %b"),
            "count": 0,
            "self": 0,
        })
    by_day = {item["key"]: item for item in days}
    start = datetime.combine(first, datetime.min.time(), tzinfo=timezone.utc).timestamp()
    try:
        rows = mu.db.fetchall(
            "SELECT created_at, created_by FROM users WHERE created_at >= ?", (start,)
        )
    except Exception:
        rows = []
    for row in rows:
        try:
            day = datetime.fromtimestamp(float(row["created_at"]), tz=timezone.utc).date()
        except (TypeError, ValueError, OSError):
            continue
        item = by_day.get(day.isoformat())
        if item is None:
            continue
        item["count"] += 1
        if (row["created_by"] or "") == "self":
            item["self"] += 1
    peak = max((item["count"] for item in days), default=0)
    for item in days:
        item["pct"] = int(round(item["count"] * 100 / peak)) if peak else 0

    def _mix(sql: str, names, key: str = "n") -> list[dict]:
        try:
            rows = mu.db.fetchall(sql)
        except Exception:
            return [{"name": name, "count": 0, "pct": 0} for name in names]
        found = {row[0]: int(row[key]) for row in rows}
        total = sum(found.values()) or 1
        return [
            {"name": name, "count": found.get(name, 0),
             "pct": round(found.get(name, 0) * 100 / total)}
            for name in names
        ]

    total_users = int(mu.db.scalar("SELECT COUNT(*) FROM users WHERE status <> 'deleted'") or 0)
    roles = _mix(
        "SELECT role, COUNT(*) AS n FROM users WHERE status <> 'deleted' GROUP BY role", ROLES
    )
    statuses = _mix(
        "SELECT status, COUNT(*) AS n FROM users WHERE status <> 'deleted' GROUP BY status",
        STATUSES,
    )

    provider_counts: dict[str, dict[str, int]] = {}
    try:
        for row in mu.db.fetchall(
            "SELECT provider, status, COUNT(*) AS n FROM user_provider_credentials "
            "GROUP BY provider, status"
        ):
            provider_counts.setdefault(row["provider"], {})[row["status"]] = int(row["n"])
    except Exception:
        provider_counts = {}
    providers = []
    for key, label in (("youtube", "YouTube Music"), ("amazon", "Amazon / Alexa")):
        found = provider_counts.get(key, {})
        connected = found.get("connected", 0)
        providers.append({
            "key": key,
            "label": label,
            "connected": connected,
            "broken": found.get("expired", 0) + found.get("error", 0),
            "pct": round(connected * 100 / total_users) if total_users else 0,
        })
    try:
        linked_users = int(mu.db.scalar(
            "SELECT COUNT(DISTINCT user_id) FROM user_provider_credentials "
            "WHERE status = 'connected'"
        ) or 0)
    except Exception:
        linked_users = 0

    return {
        "days": days,
        "peak": peak,
        "total_14d": sum(item["count"] for item in days),
        "self_14d": sum(item["self"] for item in days),
        "roles": roles,
        "statuses": statuses,
        "providers": providers,
        "linked_users": linked_users,
        "unlinked_users": max(0, total_users - linked_users),
    }


class MultiUser:
    """Owns the isolated services and the admin blueprint."""

    def __init__(self, config: MultiUserConfig, app_secret: str = "", logger=None):
        self.config = config
        if config.public_base_url.lower().startswith("https://") and not config.encryption_key:
            raise ValueError("Production HTTPS deployments require MULTIUSER_ENCRYPTION_KEY; migrate encrypted state before changing keys.")
        self.logger = logger
        self.db = Database(config.db_path)
        self.secret_box = SecretBox(config.encryption_key, fallback_secret=app_secret)
        self.accounts = AccountService(self.db, config, self.secret_box)
        self.rate_limiter = RateLimiter()
        from alexa_identity import AlexaIdentity
        self.alexa_identity = AlexaIdentity(self.accounts, app_secret)
        from .alexa_access import AlexaAccess
        self.alexa_access = AlexaAccess(self.accounts)
        default_outbox = os.path.join(
            os.path.dirname(os.path.abspath(config.db_path)), "mail-outbox"
        )
        self.mailer = Mailer.from_env(default_outbox_dir=default_outbox, logger=logger)
        self.accounts._security_notice = lambda user, heading, text: emails.send_notice(self, user, heading, text, audit="security_change")
        # Step one of sign-in at this door parks its proof here. Separate from the
        # player door's own store, so a token issued at one can never be spent at
        # the other.
        self.pending = pending.PendingSecondFactor()
        self.blueprint: Blueprint | None = None
        self.selfservice_blueprint: Blueprint | None = None
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
        # The player's own door is a separate blueprint because it is not under
        # /admin: /signup, /forgot-password and /account belong to the app root,
        # where the remote already lives.
        self.selfservice_blueprint = build_selfservice_blueprint(self)
        app.register_blueprint(self.selfservice_blueprint)
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
    # The app owns the browser sidecar and the credential files, so it supplies
    # these callables; the account layer must not import it (it is optional).
    # Resolved per request, not captured here: the app attaches the adapter after
    # the blueprint is built, because building this blueprint is part of init.
    def _youtube():
        return getattr(mu, "youtube_admin", None)

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
            if g.mu_user.get("must_change_password") and not password_change_allowed(request.path, request.method):
                return redirect(url_for("multiuser_admin.account", reauth="password"))
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
        "multiuser_admin.email_login",
        "multiuser_admin.magic_login",
    }

    @bp.before_request
    def _guard():
        if request.content_length and request.content_length > 32768:
            return jsonify(error="request_too_large"), 413
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
            "admin-request", netids.ip_bucket(_client_ip()), mu.config.request_max_per_minute, 60
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
            "theme": _theme_for(mu, g.get("mu_user")),
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

    @bp.app_template_filter("mu_device")
    def _filter_device(value):
        """The browser and platform a session's header declares.

        A session table a person reads to spot an intruder is a scanning task,
        and a raw User-Agent is the wrong shape for it; the header itself stays
        available underneath as the evidence for the name."""
        return devicename.describe_user_agent(value) or "—"

    @bp.app_template_filter("mu_json")
    def _filter_json(value):
        try:
            return json.dumps(value, indent=2, sort_keys=True)
        except (TypeError, ValueError):
            return str(value)

    # ----------------------------------------------------------- email helpers
    def _absolute(path: str) -> str:
        return mu.absolute_url(path, request.url_root)

    def _send_token_email(
        user: dict,
        purpose: str,
        token: str,
        *,
        actor: dict | None = None,
        ip: str = "",
        user_agent: str = "",
    ):
        # An invitation is the player's, so its URL belongs to the public door;
        # only the administrator sign-in link lives behind /admin.
        link_base = "" if purpose == "invite" else base
        return emails.send_token_email(
            mu, user, purpose, token, base=link_base, actor=actor, ip=ip, user_agent=user_agent
        )

    def _send_code_email(
        user: dict,
        purpose: str,
        *,
        actor: dict | None = None,
        ip: str = "",
        user_agent: str = "",
    ):
        """Issue and email a typed six-digit code. No URL is built for it.

        Invitations and administrator sign-in links keep their links; verification
        and reset are codes on both doors, because a link in an email is a
        credential a mail reader can follow without anybody typing anything.
        """
        code = mu.accounts.create_email_code(
            user["id"], purpose, user.get("email") or "", ip=ip, user_agent=user_agent
        )
        if purpose == "reauth":
            where = f"the confirmation dialog on {base}/account/"
        elif purpose == "reset":
            where = f"the reset form on {base}/reset-password/"
        elif AccountService.can_view_admin(user):
            where = f"the account page of this admin panel ({base}/account/)"
        else:
            # An administrator can send a verification code for a *player's* account,
            # and that person has no business being pointed at the admin door.
            where = "the confirm-code page of your Music Box account (/verify)"
        return emails.send_code_email(
            mu, user, purpose, code,
            where=where, actor=actor, ip=ip, user_agent=user_agent,
        )

    def _flash_mail(result, description: str, email: str = "") -> None:
        emails.flash_mail(mu, result, description, email)

    # ------------------------------------------------------------------ pages
    @bp.route("/", methods=["GET"])
    @admin_required
    def dashboard():
        counts = mu.accounts.counts()
        # list_audit floors its page size at ten for the log's own pagination, so
        # the dashboard takes the five it promises rather than re-tuning that floor.
        recent_audit, _ = mu.accounts.list_audit(per_page=5)
        recent_audit = recent_audit[:5]
        policy = mu.accounts.signup_policy()
        switch = mu.accounts.system_setting_row(SIGNUP_SWITCH_KEY)

        def _count(sql: str) -> int:
            try:
                return int(mu.db.scalar(sql) or 0)
            except Exception:
                return 0

        # A short, honest list of things that are actually wrong. The dashboard used
        # to re-render the user table, the audit table and the runtime block that
        # three other pages already own; none of that answered "is anything broken?".
        attention = []
        unverified = _count(
            "SELECT COUNT(*) FROM users WHERE status = 'active' "
            "AND COALESCE(email, '') <> '' AND email_verified = 0"
        )
        if unverified:
            attention.append({
                "text": f"{unverified} active account(s) have an unverified email address.",
                "href": url_for("multiuser_admin.users"),
                "label": "Review users",
            })
        expired = _count(
            "SELECT COUNT(*) FROM user_provider_credentials WHERE status = 'expired'"
        )
        if expired:
            attention.append({
                "text": f"{expired} linked service(s) expired and need signing in again.",
                "href": url_for("multiuser_admin.users"),
                "label": "Review users",
            })
        forced = _count(
            "SELECT COUNT(*) FROM users WHERE status = 'active' AND must_change_password = 1"
        )
        if forced:
            attention.append({
                "text": f"{forced} account(s) must still choose a new password.",
                "href": url_for("multiuser_admin.users"),
                "label": "Review users",
            })
        purges = _count("SELECT COUNT(*) FROM pending_purges")
        if purges:
            attention.append({
                "text": f"{purges} account cleanup(s) are still retrying in the background.",
                "href": url_for("multiuser_admin.system"),
                "label": "Open System",
            })
        if not policy["open"]:
            if policy["reason"] == "environment":
                reason = "Public sign-up is disabled for this whole deployment."
            elif policy["reason"] == "daily_cap":
                reason = (f"Public sign-up closed for today after {policy['count']} of "
                          f"{policy['limit']} accounts.")
            else:
                reason = "Public sign-up was closed by an administrator."
            attention.append({
                "text": reason,
                "href": url_for("multiuser_admin.system"),
                "label": "Sign-up settings",
            })

        return render_template(
            "admin/dashboard.html",
            page="dashboard",
            title="Dashboard",
            counts=counts,
            analytics=_dashboard_analytics(mu),
            recent_audit=recent_audit,
            attention=attention,
            bootstrap=mu.bootstrap_result,
            signup_policy=policy,
            signup_switch=switch,
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
            statuses=list(STATUSES) + ["deleted"],
            signup_policy=mu.accounts.signup_policy(),
            signup_switch=mu.accounts.system_setting_row(SIGNUP_SWITCH_KEY),
        )

    @bp.route("/users/new/", methods=["GET", "POST"])
    @admin_required
    def user_new():
        form = {
            "username": "",
            "email": "",
            "role": "user",
            "password": "",
            "must_change_password": False,
            "send_invite": False,
            "send_verification": True,
        }
        if request.method == "POST":
            form = {
                "username": (request.form.get("username") or "").strip(),
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
                        result = _send_code_email(
                            user, "verify", actor=g.mu_user,
                            ip=_client_ip(), user_agent=_user_agent(),
                        )
                        _flash_mail(result, "Verification code", user["email"])
                    if generated_password and not form["send_invite"]:
                        return render_template("admin/password_result.html", title="Account password", password=form["password"], target_id=user["id"], must_change_password=form["must_change_password"])
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
        user = mu.accounts.get_user(user_id, include_deleted=True)
        if not user:
            flash("User not found.", "error")
            return redirect(url_for("multiuser_admin.users"))
        if not AccountService.can_manage_user(g.mu_user, user) and g.mu_user["id"] != user_id:
            return _forbidden()
        providers = mu.accounts.provider_status(user_id)
        sessions = mu.accounts.list_sessions(user_id)
        page_now = time.time()
        alexa_tokens = mu.alexa_access.metadata(user_id)
        from .alexa_access import AccessDenied
        for token in alexa_tokens:
            token['state'] = 'revoked' if token['revoked_at'] else ('expired' if token['expires_at'] <= page_now else 'inactive')
            if token['state'] == 'inactive':
                try:
                    mu.alexa_access.current(token['id'])
                    token['state'] = 'active'
                except AccessDenied:
                    pass
        return render_template(
            "admin/user_detail.html",
            page="users",
            title=user["username"],
            user=user,
            deleted=user["status"] == "deleted",
            can_restore=g.mu_user.get("role") == "owner",
            sessions=sessions,
            page_now=page_now,
            live_session_count=sum(not row["revoked_at"] and row["expires_at"] > page_now for row in sessions),
            providers=providers,
            alexa_token=request.environ.get("musicbox.new_alexa_token"),
            alexa_tokens=alexa_tokens,
            alexa_eligible=mu.alexa_access.eligible(user),
            has_active_alexa_token=any(row["state"] == "active" for row in alexa_tokens),
            audit=[],
            roles=ROLES,
            allowed_roles=[role for role in ROLES if AccountService.can_assign_role(g.mu_user, role)],
            status_options=AccountService.status_options(user["status"]),
            can_manage=AccountService.can_manage_user(g.mu_user, user) and user["status"] != "deleted",
            is_self=g.mu_user["id"] == user_id,
        )

    @bp.route("/users/<user_id>/alexa/", methods=["POST"])
    @admin_required
    def user_alexa(user_id):
        user = mu.accounts.get_user(user_id)
        if not user or not AccountService.can_manage_user(g.mu_user, user):
            return _forbidden()
        action = request.form.get("action")
        try:
            if action == "access":
                mu.alexa_access.set_enabled(user_id, request.form.get("enabled") == "1")
            elif action == "generate":
                request.environ["musicbox.new_alexa_token"] = mu.alexa_access.rotate(user_id)
            elif action == "revoke":
                mu.alexa_access.revoke(user_id)
            else:
                return _forbidden()
            mu.db.audit(g.mu_user["id"], g.mu_user["username"], "alexa_" + action, user_id)
        except ValueError as exc:
            flash(str(exc), "error")
        if request.environ.get("musicbox.new_alexa_token"):
            response = current_app.make_response(user_detail(user_id))
            response.headers["Cache-Control"] = "no-store"
            response.headers["Referrer-Policy"] = "no-referrer"
            return response
        return redirect(url_for("multiuser_admin.user_detail", user_id=user_id))

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
            if generated:
                return render_template("admin/password_result.html", title="Temporary password", password=password, target_id=user_id)
            flash("Password reset.", "success")
            flash("Every device on this account was signed out.", "info")
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

    @bp.route("/users/<user_id>/password-change/", methods=["POST"])
    @admin_required
    def user_password_change_flag(user_id):
        state = (request.form.get("state") or "").strip()
        if state not in {"require", "clear"}:
            flash("Choose whether the password change is required or cleared.", "error")
            return redirect(url_for("multiuser_admin.user_detail", user_id=user_id))
        try:
            mu.accounts.set_must_change_password(
                g.mu_user, user_id, state == "require",
                ip=_client_ip(), user_agent=_user_agent(),
            )
            flash(
                "This account must change its password at the next sign-in."
                if state == "require"
                else "Password change is no longer required.",
                "success",
            )
        except AccountError as exc:
            flash(str(exc), "error")
        return redirect(url_for("multiuser_admin.user_detail", user_id=user_id))

    # ------------------------------------------------------- own second factor
    def _reauth_form() -> tuple[bool, dict]:
        """The one credential the dialog's own choice names, never both.

        Reading whichever field happened to be in the POST would let a leftover
        password box satisfy an email-code submit, or a stale authenticator code
        be tried as an email code."""
        by_email = (request.form.get("verify") or "") == "email"
        return by_email, {
            "password": "" if by_email else request.form.get("current_password") or "",
            "email_code": (request.form.get("code") or "") if by_email else "",
        }

    @bp.route("/account/totp/begin/", methods=["POST"])
    @admin_required
    def account_totp_begin():
        try:
            mu.accounts.begin_totp_enrollment(
                g.mu_user, session_token=session.get("mu_sid") or "",
                current_password=request.form.get("current_password") or "",
                issuer=mu.config.app_name,
                ip=_client_ip(), user_agent=_user_agent(),
            )
            flash("Scan the code with your authenticator app, then type the code it shows.", "success")
        except AccountError as exc:
            flash(str(exc), "error")
        return redirect(url_for("multiuser_admin.account"))

    @bp.route("/account/totp/reauth/send/", methods=["POST"])
    @admin_required
    def account_totp_reauth_send():
        """Post the code that unlocks a dialog.

        A separate endpoint because sending is not the action itself: it only buys
        the right to ask for that action afterwards. With JS the answer is JSON so
        the dialog stays put and a toast says "sent"; without it the redirect comes
        back with the same dialog open on the email tab."""
        slug = request.form.get("action") or ""
        wants_json = request.headers.get("X-Requested-With") == "fetch"
        user = mu.accounts.get_user(g.mu_user["id"])

        def answer(category: str, message: str, target: str):
            if wants_json:
                return jsonify(ok=category != "error", kind=category, message=message)
            flash(message, category)
            return redirect(target)

        if slug not in {"disable", "replace", "reset", "email"} or not user:
            return answer("error", "That request could not be understood.",
                          url_for("multiuser_admin.account"))
        if not user.get("email"):
            return answer(
                "error",
                "This account has no email address, so a code cannot be sent. Use your password.",
                url_for("multiuser_admin.account", reauth=slug),
            )
        if mu.accounts.recent_email_token_count(user["id"], "reauth", 60):
            return answer(
                "warning",
                "A code was sent less than a minute ago. Check your inbox, or use your password.",
                url_for("multiuser_admin.account", reauth=slug, way="email"),
            )
        result = _send_code_email(
            user, "reauth", actor=g.mu_user, ip=_client_ip(), user_agent=_user_agent()
        )
        category, message = emails.mail_message(mu, result, "Confirmation code", user["email"])
        return answer(
            category, message,
            url_for("multiuser_admin.account", reauth=slug, way="email", sent=1),
        )

    @bp.route("/account/totp/replace/", methods=["POST"])
    @admin_required
    def account_totp_replace():
        """Start again with a different authenticator app.

        The live secret keeps working until the new one is confirmed, so an
        abandoned setup cannot lock anybody out of their own sign-in."""
        slug = "replace"
        by_email, credentials = _reauth_form()
        back = url_for(
            "multiuser_admin.account", reauth=slug,
            way="email" if by_email else "password",
        )
        try:
            mu.accounts.begin_totp_enrollment(
                g.mu_user, session_token=session.get("mu_sid") or "",
                issuer=mu.config.app_name,
                current_password=request.form.get("current_password") or "",
                email_code=request.form.get("code") or "", allow_existing=True,
                ip=_client_ip(), user_agent=_user_agent(),
            )
            flash("Scan the new authenticator and confirm its code. Your current one still works until you do.", "success")
            return redirect(url_for("multiuser_admin.account"))
        except AccountError as exc:
            flash(str(exc), "error")
            return redirect(back)

    @bp.route("/account/totp/qr/", methods=["GET"])
    @admin_required
    def account_totp_qr():
        """The provisioning URI as an image, never as text.

        Same rules as the player door: the URI *is* the secret, so it is only ever
        the ``src`` of one same-origin image on a no-store response."""
        enrollment = mu.accounts.pending_totp_enrollment(g.mu_user, session_token=session.get("mu_sid") or "")
        if not enrollment:
            return Response("", status=409, mimetype="text/plain")
        try:
            import qrcode
            import qrcode.image.svg
        except Exception:  # noqa: BLE001 - optional dependency: degraded, not broken
            if mu.logger:
                mu.logger.exception("[multiuser] qrcode package unavailable")
            return Response("", status=503, mimetype="text/plain")
        image = qrcode.make(enrollment["otpauth_uri"], image_factory=qrcode.image.svg.SvgImage)
        buffer = io.BytesIO()
        image.save(buffer)
        response = Response(buffer.getvalue(), mimetype="image/svg+xml")
        response.headers["Cache-Control"] = "no-store"
        return response

    @bp.route("/account/totp/confirm/", methods=["POST"])
    @admin_required
    def account_totp_confirm():
        try:
            mu.accounts.confirm_totp_enrollment(
                g.mu_user, session_token=session.get("mu_sid") or "", code=request.form.get("code") or "",
                ip=_client_ip(), user_agent=_user_agent(),
            )
            flash("Two-factor sign-in is on. You will be asked for a code when you sign in.", "success")
        except AccountError as exc:
            flash(str(exc), "error")
        return redirect(url_for("multiuser_admin.account"))

    @bp.route("/account/totp/cancel/", methods=["POST"])
    @admin_required
    def account_totp_cancel():
        try:
            mu.accounts.cancel_totp_enrollment(
                g.mu_user, ip=_client_ip(), user_agent=_user_agent()
            )
            flash("Two-factor setup was cancelled.", "info")
        except AccountError as exc:
            flash(str(exc), "error")
        return redirect(url_for("multiuser_admin.account"))

    @bp.route("/account/totp/disable/", methods=["POST"])
    @admin_required
    def account_totp_disable():
        slug = "disable"
        by_email, credentials = _reauth_form()
        back = url_for(
            "multiuser_admin.account", reauth=slug,
            way="email" if by_email else "password",
        )
        try:
            mu.accounts.disable_totp(
                g.mu_user, session_token=session.get("mu_sid") or "", current_password=request.form.get("current_password") or "",
                email_code=request.form.get("code") or "",
                ip=_client_ip(), user_agent=_user_agent(),
            )
            flash("Two-factor sign-in is off. Sign in with your password alone from now on.", "success")
            return redirect(url_for("multiuser_admin.account"))
        except AccountError as exc:
            flash(str(exc), "error")
            return redirect(back)

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
        result = _send_code_email(
            user, "verify", actor=g.mu_user, ip=_client_ip(), user_agent=_user_agent()
        )
        _flash_mail(result, "Verification code", user["email"])
        if result.status == "sent":
            flash(
                "The code is in their inbox. They type it into their own account page "
                "-- there is no link for anyone to click.",
                "info",
            )
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
            flash(f"Signed out {count} device{'s' if count != 1 else ''}.", "success")
        except AccountError as exc:
            flash(str(exc), "error")
        return redirect(url_for("multiuser_admin.user_detail", user_id=user_id))

    @bp.route("/users/<user_id>/provider/<provider>/disconnect/", methods=["POST"])
    @admin_required
    def user_provider_disconnect(user_id, provider):
        label = "YouTube Music" if provider == "youtube" else "Amazon"
        try:
            failures, was_linked = mu.accounts.clear_provider_credentials(
                g.mu_user, user_id, provider, ip=_client_ip(), user_agent=_user_agent()
            )
        except AccountError as exc:
            flash(str(exc), "error")
        else:
            if failures:
                flash(
                    f"{label} sign-in was removed, but this server could not delete "
                    f"everything yet: {'; '.join(failures)}. Try again, or delete the "
                    f"account to force a complete purge.",
                    "warning",
                )
            elif not was_linked:
                flash(
                    f"Nothing was connected to {label} on this account, so there was "
                    f"nothing to remove.",
                    "info",
                )
            else:
                flash(
                    f"{label} sign-in was removed, and its saved cookies and browser "
                    f"profile on this server were deleted.",
                    "success",
                )
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

    @bp.route("/users/<user_id>/permanent-delete/", methods=["POST"])
    @admin_required
    def user_permanent_delete(user_id):
        try:
            mu.accounts.permanently_delete_user(
                g.mu_user, user_id, request.form.get("confirmation") or "",
                ip=_client_ip(), user_agent=_user_agent(),
            )
            flash("Account permanently deleted. It cannot be restored.", "success")
            return redirect(url_for("multiuser_admin.users", status="deleted"))
        except AccountError as exc:
            flash(str(exc), "error")
            if mu.accounts.get_user(user_id, include_deleted=True):
                return redirect(url_for("multiuser_admin.user_detail", user_id=user_id))
            return redirect(url_for("multiuser_admin.users", status="deleted"))

    @bp.route("/users/<user_id>/restore/", methods=["POST"])
    @admin_required
    def user_restore(user_id):
        try:
            mu.accounts.restore_user(g.mu_user, user_id, ip=_client_ip(), user_agent=_user_agent())
            flash("User restored in disabled state. Set a password, then activate the account.", "success")
        except AccountError as exc:
            flash(str(exc), "error")
        return redirect(url_for("multiuser_admin.user_detail", user_id=user_id))

    @bp.route("/audit/", methods=["GET"])
    @admin_required
    def audit():
        page = _parse_page()
        action = (request.args.get("action") or "").strip()
        actor = (request.args.get("actor") or "").strip()
        target = (request.args.get("target") or "").strip()
        rows, total = mu.accounts.list_audit(page, AUDIT_PAGE_SIZE, action, actor, target)
        try:
            accounts = [
                dict(row)
                for row in mu.db.fetchall(
                    "SELECT id, username FROM users "
                    "WHERE status <> 'deleted' ORDER BY username COLLATE NOCASE LIMIT 500"
                )
            ]
        except Exception:
            accounts = []
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
            accounts=accounts,
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

    @bp.route("/youtube/", methods=["GET"])
    @admin_required
    def youtube_downloads():
        """The one Google account that supplies download cookies and fallback.

        Per-account YouTube links are the account holder's own business and are
        made from that person's account page; this page only owns the shared
        server account.
        """
        hooks = _youtube()
        return render_template(
            "admin/youtube.html",
            page="youtube",
            title="YouTube downloads",
            youtube=hooks.server_status() if hooks else None,
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
            signup_policy=mu.accounts.signup_policy(),
            signup_switch=mu.accounts.system_setting_row(SIGNUP_SWITCH_KEY),
        )

    @bp.route("/youtube/signin/", methods=["POST"])
    @admin_required
    def youtube_downloads_signin():
        """Ask the download-cookies account to sign in now instead of waiting."""
        hooks = _youtube()
        if hooks is None:
            flash("Automatic YouTube sign-in is not available on this server.", "error")
            return redirect(url_for("multiuser_admin.youtube_downloads"))
        if g.mu_user["role"] != "owner":
            flash("Only an owner can start the download account's YouTube sign-in.", "error")
            return redirect(url_for("multiuser_admin.youtube_downloads"))
        state = hooks.server_status()
        if not state.get("configured"):
            flash(
                "No download Google account is configured (YOUTUBE_LOGIN_USERNAME, "
                "YOUTUBE_LOGIN_PASSWORD, YOUTUBE_LOGIN_TOTP_CODE).",
                "warning",
            )
            return redirect(url_for("multiuser_admin.youtube_downloads"))
        if hooks.start_server_signin() != "started":
            flash("Automatic sign-in is switched off (YOUTUBE_AUTO_LOGIN_ENABLED=0).", "warning")
            return redirect(url_for("multiuser_admin.youtube_downloads"))
        mu.accounts.audit(
            g.mu_user, "youtube_server_signin_started", None, {}, _client_ip(), _user_agent(),
        )
        flash(
            "The download account is signing in to YouTube Music now. Reload this page "
            "for the result.",
            "success",
        )
        return redirect(url_for("multiuser_admin.youtube_downloads"))

    @bp.route("/users/signup/", methods=["POST"])
    @admin_required
    def users_signup():
        """Switch the public sign-up door without touching the environment.

        Owner-only, like backups: the switch governs who may obtain an account on
        this deployment, which is not an ordinary admin action. The environment
        stays the ceiling -- ``set_signup_switch`` refuses to open a door the
        deployment closed.

        It lives under ``/users/`` because that is where the decision is made:
        a person closing the door is looking at the list of people who have
        already come through it.
        """
        if g.mu_user["role"] != "owner":
            flash("Only an owner can change the sign-up policy.", "error")
            return redirect(url_for("multiuser_admin.users"))
        state = (request.form.get("state") or "").strip().lower()
        if state not in ("enable", "disable"):
            flash("Choose whether to open or close public sign-ups.", "error")
            return redirect(url_for("multiuser_admin.users"))
        opened = state == "enable"
        if opened and not mu.config.self_signup_enabled:
            flash(
                "This deployment sets MULTIUSER_SELF_SIGNUP_ENABLED=0, so public "
                "sign-ups cannot be opened from the panel.",
                "error",
            )
            return redirect(url_for("multiuser_admin.users"))
        try:
            mu.accounts.set_signup_switch(
                g.mu_user, opened, _client_ip(), _user_agent()
            )
        except AccountError as exc:
            flash(str(exc), "error")
            return redirect(url_for("multiuser_admin.users"))
        flash(
            "Public sign-ups are open again." if opened else "Public sign-ups are now closed.",
            "success",
        )
        return redirect(url_for("multiuser_admin.users"))

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
                return redirect(url_for("multiuser_admin.account", reauth="password"))
            try:
                mu.accounts.change_own_password(
                    g.mu_user, current, new, ip=_client_ip(), user_agent=_user_agent(),
                    # Without this the session the change was made from is
                    # revoked too, so "Password changed." was followed by a
                    # bounce to the sign-in page.
                    keep_sid_hash=session.get("mu_sid") or "",
                )
                flash("Password changed.", "success")
                return redirect(url_for("multiuser_admin.account"))
            except AccountError as exc:
                flash(str(exc), "error")
                return redirect(url_for("multiuser_admin.account", reauth="password"))
        return render_template(
            "admin/account.html",
            page="account",
            title="My account",
            user=mu.accounts.get_user(g.mu_user["id"]),
            # Only live sessions, told apart by a fingerprint: the row can open
            # its own dialog and name itself in the revoke action without the
            # browser ever seeing a session id.
            sessions=mu.accounts.session_summaries(
                g.mu_user["id"], current_sid_hash=(g.mu_session or {}).get("sid_hash", "")
            ),
            # Which verification dialog to reopen, and on which tab: a page reload
            # from "send code" or a refused password would otherwise drop the person
            # back onto the card and make them start the conversation again.
            reauth=request.args.get("reauth") or "",
            way=request.args.get("way") or "",
            sent=(request.args.get("sent") or "") == "1",
            code_minutes=mu.config.email_code_minutes,
        )

    @bp.route("/account/password/reset/", methods=["POST"])
    @admin_required
    def account_password_reset():
        """Set a new password proved by the mailbox and, when armed, the authenticator."""
        back = url_for("multiuser_admin.account", reauth="reset")
        new = request.form.get("new_password") or ""
        if new != (request.form.get("confirm_password") or ""):
            flash("New password and confirmation do not match.", "error")
            return redirect(back)
        try:
            mu.accounts.reset_own_password(
                g.mu_user,
                email_code=(request.form.get("code") or "").strip(),
                new_password=new,
                totp_code=(request.form.get("totp_code") or "").strip(),
                ip=_client_ip(), user_agent=_user_agent(),
                keep_sid_hash=session.get("mu_sid") or "",
            )
            flash("Password reset.", "success")
            return redirect(url_for("multiuser_admin.account"))
        except AccountError as exc:
            flash(str(exc), "error")
            return redirect(back)

    @bp.route("/account/profile/", methods=["POST"])
    @admin_required
    def account_profile():
        previous_email = (g.mu_user.get("email") or "").strip().lower()
        try:
            user = mu.accounts.update_own_profile(
                g.mu_user,
                email=request.form.get("email") or "",
                current_password=request.form.get("current_password") or "",
                email_code=request.form.get("email_code") or "",
                ip=_client_ip(),
                user_agent=_user_agent(),
            )
            flash("Confirm the proposed address before it replaces your current email.", "success")
            if (
                user.get("pending_email")
            ):
                result = _send_code_email(
                    user, "verify", actor=g.mu_user,
                    ip=_client_ip(), user_agent=_user_agent(),
                )
                _flash_mail(result, "Verification code", user["email"])
        except AccountError as exc:
            flash(str(exc), "error")
            return redirect(url_for("multiuser_admin.account", reauth="email"))
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
        result = _send_code_email(
            user, "verify", actor=g.mu_user, ip=_client_ip(), user_agent=_user_agent()
        )
        _flash_mail(result, "Verification code", user["email"])
        return redirect(url_for("multiuser_admin.account"))

    @bp.route("/account/email/confirm/", methods=["POST"])
    @admin_required
    def account_email_confirm():
        """Type the six digits from the email, for this administrator's own address."""
        code = (request.form.get("code") or "").strip()
        consumed = mu.accounts.consume_email_code(
            g.mu_user["id"], "verify", code
        )
        if not consumed:
            flash(
                "That code did not work. Codes are single-use, expire after "
                f"{mu.config.email_code_minutes} minutes and stop working after "
                f"{mu.config.email_code_attempts} wrong tries. Ask for a new one.",
                "error",
            )
            return redirect(url_for("multiuser_admin.account"))
        try:
            mu.accounts.verify_email_with_token(
                g.mu_user["id"], ip=_client_ip(), user_agent=_user_agent()
            )
        except AccountError as exc:
            flash(str(exc), "error")
            return redirect(url_for("multiuser_admin.account"))
        flash("Email address confirmed.", "success")
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
            flash(f"Signed out {count} device{'s' if count != 1 else ''}, including this one.", "success")
        except AccountError as exc:
            flash(str(exc), "error")
            return redirect(url_for("multiuser_admin.account"))
        return redirect(login_url)

    @bp.route("/account/sessions/<fingerprint>/revoke/", methods=["POST"])
    @admin_required
    def account_session_revoke(fingerprint):
        try:
            _, killed_current = mu.accounts.revoke_own_sessions(
                g.mu_user,
                only_prefix=fingerprint,
                keep_sid_hash=session.get("mu_sid") or "",
                ip=_client_ip(), user_agent=_user_agent(),
            )
        except AccountError as exc:
            flash(str(exc), "error")
            return redirect(url_for("multiuser_admin.account"))
        if killed_current:
            session.pop("mu_sid", None)
            session.pop("mu_user_id", None)
            flash("That was this browser, so you are signed out.", "success")
            return redirect(login_url)
        flash("Device signed out.", "success")
        return redirect(url_for("multiuser_admin.account"))

    @bp.route("/theme/", methods=["POST"])
    @admin_required
    def set_theme():
        """Switch the panel between its dark and light palette.

        Cosmetic, so it asks for nothing beyond the session that is already
        here -- but it returns to the page it was pressed from, because a
        person setting a theme mid-audit does not want to be sent to the
        dashboard.  The ``next`` value has to be inside this blueprint: an
        open redirect dressed up as a preference would be a new bug.
        """
        wanted = (request.form.get("state") or "").strip().lower()
        target = request.form.get("next") or ""
        if not (target.startswith(base) and not target.startswith("//")):
            target = url_for("multiuser_admin.dashboard")
        if wanted not in ("dark", "light"):
            flash("Choose either the dark theme or the light theme.", "error")
            return redirect(target)
        mu.accounts.set_preference(g.mu_user["id"], THEME_KEY, wanted)
        return redirect(target)

    # ------------------------------------------------------------------ auth
    @bp.route("/login/", methods=["GET", "POST"])
    def login():
        def door(next_target: str, step: str = "", pending: str = "", status: int | None = None):
            response = render_template(
                "admin/login.html", title="Admin sign in", next_target=next_target,
                step=step, pending=pending,
            )
            return (response, status) if status is not None else response

        if request.method == "GET":
            raw_sid = session.get("mu_sid")
            if raw_sid:
                _, user = mu.accounts.validate_session(raw_sid)
                if user and AccountService.can_view_admin(user):
                    return redirect(f"{base}/")
            return door(_safe_next(request.args.get("next", "")))

        ip = _client_ip()
        bind = pending.bind_value(netids.ip_bucket(ip), _user_agent())

        # Step 2: an administrator who already passed the password.
        if (request.form.get("step") or "") == "code":
            record = mu.pending.consume(request.form.get("pending", ""), bind)
            next_target = _safe_next(record.get("next", "") if record else "")
            user = mu.accounts.get_user(record.get("uid", "")) if record else None
            # Re-read the account rather than trusting a five-minute-old answer:
            # an admin demoted, disabled or 2FA-disabled between the steps must
            # not reach the panel.
            if (
                not record
                or not user
                or user.get("status") != "active"
                or not AccountService.can_view_admin(user)
                or record.get("security_version") != mu.accounts.security_version(user["id"])
                or not mu.accounts.totp_is_enabled(user)
                or not mu.accounts.verify_totp_code(user["id"], str(request.form.get("code") or "").strip())
            ):
                mu.accounts.audit(
                    user, "admin_login_2fa_failed", (user or {}).get("id") or "",
                    {"reason": "code" if record else "expired"}, ip, _user_agent(),
                )
                # One flat answer, and the pending proof is spent either way: this
                # step is not a code oracle with a five-minute timer.
                flash("That sign-in needs your password again.", "error")
                return door(next_target, status=401)
            raw_sid = mu.accounts.create_session(user, ip, _user_agent())
            complete_login(mu, user, ip)
            replace_identity()
            session["mu_sid"] = raw_sid
            session["mu_user_id"] = user["id"]
            session.permanent = True
            rotate_csrf()
            mu.accounts.audit(user, "admin_login_2fa", user["id"], {}, ip, _user_agent())
            return redirect(next_target)

        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        next_target = _safe_next(request.form.get("next", ""))
        account_key = netids.identifier_bucket(username)
        ip_key = netids.ip_bucket(ip)
        # Two independent budgets: per account and per source address.
        account_allowed, account_retry = mu.rate_limiter.allow(
            "login-account", account_key, mu.config.login_max_attempts, mu.config.login_window_seconds
        )
        ip_allowed, ip_retry = mu.rate_limiter.allow(
            "login-ip", ip_key, mu.config.login_max_attempts * 10, mu.config.login_window_seconds
        )
        if not account_allowed or not ip_allowed:
            retry = max(account_retry, ip_retry, 1)
            mu.accounts.audit(
                None, "login_rate_limited", None,
                {"retry_after": retry, "attempted": (username or "")[:120]},
                ip, _user_agent(),
            )
            flash(f"Too many sign-in attempts. Try again in {retry}s.", "error")
            return door(next_target, status=429)

        user, error = mu.accounts.authenticate(username, password, ip, _user_agent())
        if not user:
            flash(error, "error")
            return door(next_target, status=401)
        if not AccountService.can_view_admin(user):
            mu.accounts.audit(
                user, "admin_login_denied", user["id"], {"reason": "role"}, ip, _user_agent()
            )
            flash("This account does not have admin access.", "error")
            return door(next_target, status=403)

        # Successful authentication clears the per-account and per-IP budgets:
        # they exist to slow guessing, not to cap legitimate sign-ins.  These keys
        # must be the bucketed ones the allow() above spent, or the reset is a
        # silent no-op and the two doors stop sharing a cleared budget.

        if mu.accounts.totp_is_enabled(user):
            # The password buys a wait, not a session.
            token = mu.pending.issue(
                {"kind": "admin", "uid": user["id"], "next": next_target, "security_version": user["security_version"]}, bind
            )
            mu.accounts.audit(user, "admin_login_2fa", user["id"], {"step": "issued"}, ip, _user_agent())
            return door(next_target, step="code", pending=token)

        raw_sid = mu.accounts.create_session(user, ip, _user_agent())
        complete_login(mu, user, ip)
        replace_identity()
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
        allowed, retry = mu.rate_limiter.allow(
            "forgot-ip", netids.ip_bucket(ip), 10, mu.config.login_window_seconds
        )
        id_allowed, id_retry = mu.rate_limiter.allow(
            "forgot-id", netids.identifier_bucket(identifier), 3, mu.config.login_window_seconds
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
            result = _send_code_email(user, "reset", ip=ip, user_agent=_user_agent())
            mu.accounts.audit(
                None, "password_reset_code_requested", user["id"],
                {"delivery": result.status}, ip, _user_agent(),
            )
        elif identifier:
            mu.accounts.audit(
                None, "password_reset_requested_unknown", None,
                {"identifier": identifier[:120]}, ip, _user_agent(),
            )
        # Enumeration-safe response regardless of whether the account exists.
        flash(
            "If an account matches that address, a six-digit reset code has been sent. "
            f"It expires in {mu.config.email_code_minutes} minutes.",
            "success",
        )
        return redirect(f"{base}/reset-password/")

    @bp.route("/reset-password/", methods=["GET", "POST"])
    def reset_password():
        """Choose a new password by typing the emailed code. No link involved."""
        ip = _client_ip()
        if request.method == "GET":
            return render_template(
                "admin/reset_password.html",
                title="Choose a new password",
                identifier=request.args.get("identifier") or "",
            )

        allowed, retry = mu.rate_limiter.allow("code-ip", netids.ip_bucket(ip), 10, 900)
        identifier = (request.form.get("identifier") or "").strip()
        id_allowed, id_retry = mu.rate_limiter.allow(
            "code-id", netids.identifier_bucket(identifier), 5, 900
        )
        if not allowed or not id_allowed:
            flash(
                f"Too many tries. Wait {max(retry, id_retry, 1)} seconds and enter the "
                "code again.",
                "error",
            )
            return render_template(
                "admin/reset_password.html", title="Choose a new password", identifier=identifier
            ), 429

        user = (
            mu.accounts.find_user_by_email(identifier)
            if "@" in identifier
            else mu.accounts.get_user_by_username(identifier)
        )
        password = request.form.get("password") or ""
        confirm = request.form.get("confirm_password") or ""

        def rejected(message: str, status: int = 410):
            flash(message, "error")
            return render_template(
                "admin/reset_password.html",
                title="Choose a new password",
                identifier=identifier,
                code=request.form.get("code") or "",
            ), status

        if not user or user.get("status") != "active":
            # "no such account" and "wrong code" are one answer, and it never says
            # which of the two the panel could not work out.
            return rejected(
                "That code did not work for that account. Codes are single-use and "
                f"expire after {mu.config.email_code_minutes} minutes."
            )
        if password != confirm:
            flash("Password and confirmation do not match.", "error")
            return render_template(
                "admin/reset_password.html",
                title="Choose a new password",
                identifier=identifier,
                code=request.form.get("code") or "",
            ), 400
        problem = validate_password(password, (user or {}).get("username") or "")
        if problem:
            return rejected(problem, 422)
        consumed = mu.accounts.consume_email_code(
            user["id"], "reset", request.form.get("code") or ""
        )
        if not consumed:
            return rejected(
                "That code did not work. Codes are single-use, expire after "
                f"{mu.config.email_code_minutes} minutes and stop working after "
                f"{mu.config.email_code_attempts} wrong tries. Request a new one."
            )
        try:
            mu.accounts.reset_password_with_token(
                user["id"], password, ip=ip, user_agent=_user_agent()
            )
        except AccountError as exc:
            return rejected(str(exc), 400)
        flash("Password reset. Sign in with your new password.", "success")
        return redirect(login_url)

    @bp.route("/email-login/", methods=["GET", "POST"])
    def email_login():
        if not mu.config.magic_link_enabled:
            flash("Email sign-in links are disabled.", "error")
            return redirect(login_url)
        if request.method == "GET":
            return render_template("admin/email_login.html", title="Email sign-in link")
        identifier = (request.form.get("identifier") or "").strip()
        ip = _client_ip()
        allowed, retry = mu.rate_limiter.allow(
            "magic-ip", netids.ip_bucket(ip), 10, mu.config.login_window_seconds
        )
        id_allowed, id_retry = mu.rate_limiter.allow(
            "magic-id", netids.identifier_bucket(identifier), 3, mu.config.login_window_seconds
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

    @bp.route("/magic/<token>", methods=["GET", "POST"])
    def magic_login(token):
        if request.method == "GET":
            if not mu.accounts.peek_email_token(token, "magic"):
                return render_template("admin/error.html", status=410, message="This sign-in link is expired or invalid."), 410
            return render_template("admin/magic_confirm.html", title="Confirm sign-in")
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
                    message="Only an active owner can access this panel.",
                    action_url=login_url,
                    action_label="Back to sign in",
                ),
                403,
            )
        if mu.accounts.totp_is_enabled(user):
            bind = pending.bind_value(netids.ip_bucket(_client_ip()), _user_agent())
            token = mu.pending.issue({'kind': 'admin', 'uid': user['id'],
                'security_version': user['security_version'], 'next': base + '/'}, bind)
            return render_template('admin/login.html', title='Verify sign-in', step='code', pending=token, next_target=base + '/')
        if not user.get("email_verified"):
            mu.accounts.verify_email_with_token(user["id"], ip=_client_ip(), user_agent=_user_agent())
        # Retire any session already active in this browser before replacing it.
        previous = session.get("mu_sid")
        if previous:
            previous_session, _ = mu.accounts.validate_session(previous)
            if previous_session:
                mu.accounts.revoke_session(previous_session["sid_hash"])
        raw_sid = mu.accounts.create_session(user, _client_ip(), _user_agent())
        replace_identity()
        session["mu_sid"] = raw_sid
        session["mu_user_id"] = user["id"]
        session.permanent = True
        rotate_csrf()
        mu.accounts.audit(user, "magic_login", user["id"], {}, _client_ip(), _user_agent())
        flash("Signed in with a one-time email link.", "success")
        return redirect(f"{base}/")

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
