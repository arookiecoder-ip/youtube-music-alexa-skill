"""Self-service accounts on the player door.

Sign-up, "forgot password", the ``/account`` page and the ``/api/account`` JSON
behind it. The administrator panel (``/admin``) is for running the server; this
module is for the person who owns one account on it and must be able to create,
secure and delete that account without asking anyone.

Two shapes on purpose: the door pages (sign-up, reset) are plain HTML form POSTs
so they work with JavaScript disabled, while everything that touches a live
account is JSON. ``server.py`` decides which of these paths may be reached
anonymously (``_PUBLIC_PATHS`` / ``_PUBLIC_PREFIXES``) and which its session
cookie authorizes (``_SESSION_PREFIXES``); this blueprint re-checks identity and
CSRF itself, so a middleware edit can never silently open an account endpoint.

Nothing here imports ``server.py`` -- the layer stays optional.
"""
from __future__ import annotations

import hmac
import os
import secrets
import time
from functools import wraps
from urllib.parse import urlencode

from flask import (
    Blueprint,
    Response,
    g,
    jsonify,
    make_response,
    redirect,
    render_template,
    request,
    session,
)

from . import emails, netids
from .accounts import AccountError, AccountService
from .security import PASSWORD_MIN_LENGTH, validate_password

TEMPLATE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates"
)
STATIC_DIR = os.path.join(TEMPLATE_DIR, "static")

# A token of its own, not the admin panel's: signing in at /admin rotates that
# one, and a player mid-way through the account page must not fail CSRF because
# an administrator used the other door in the same browser.
CSRF_KEY = "mu_ss_csrf"
SIGNUP_NONCE_KEY = "mu_ss_signup"
# Which account this browser just asked to verify, so the code page can say "we sent
# a code for @alice" and prefill the username.  It is an identifier, not a
# credential: nothing can be done with it without the code, which is why it is safe
# to keep in a cookie the browser already holds.
PENDING_VERIFY_KEY = "mu_ss_pending_verify"
# The same stored preference the administrator panel writes, deliberately: an account
# that chose the light palette in one surface gets it in the other. Named here rather
# than imported from ``admin.py`` because that module imports this one, and a
# cross-blueprint import in the other direction would be a cycle.
THEME_KEY = "admin_theme"
THEMES = ("dark", "light")

USERNAME_TAKEN = "That username is already in use."
EMAIL_TAKEN = "That email address is already in use."
# The email collision is the one failure that must not name the field: it proves
# an address is registered. A username is the login handle and is already visible
# to anyone who can sign in, so that one is safe to state plainly.
EMAIL_TAKEN_GENERIC = (
    "We could not create that account with those details. If this email address "
    "already has an account, use Forgot password to get into it."
)

# scope -> (limit, window_seconds); a ``None`` window means login_window_seconds,
# so the buckets shared with the administrator door really are one budget.
# Deliberately not env-tunable: these are the ceilings that protect the door.
# The operator-facing policy is MULTIUSER_SELF_SIGNUP_ENABLED and
# MULTIUSER_MAX_SELF_SIGNUPS_PER_DAY.
_LIMITS: dict[str, tuple[int, int | None]] = {
    "signup-ip": (5, 3600),
    "signup-id": (3, 86400),
    "resend-ip": (10, 900),
    "resend-id": (3, 900),
    "forgot-ip": (10, None),
    "forgot-id": (3, None),
    "reset-ip": (20, 900),
    # Typing a code is where an attacker actually gets to guess, so this is the
    # tightest budget on the door: six digits, five tries per identifier, and a
    # per-IP ceiling on top so one address cannot sweep the whole username list.
    "code-ip": (10, 900),
    "code-id": (5, 900),
    "account-password": (5, 900),
    "totp-begin": (3, 3600),
    "totp-confirm": (10, 3600),
    "totp-disable": (5, 3600),
    "account-delete": (3, 86400),
}

_SESSION_SURFACE = ("/account", "/api/account")

_ASSET_VERSIONS: dict[str, str] = {}


def _asset_version(relative: str) -> str:
    """Per-file cache buster, so a CSS-only deploy does not have to guess."""
    version = _ASSET_VERSIONS.get(relative)
    if version is None:
        try:
            version = str(int(os.path.getmtime(os.path.join(STATIC_DIR, relative))))
        except OSError:
            version = "0"
        _ASSET_VERSIONS[relative] = version
    return version


def _mask(email: str) -> str:
    """Echo an address back without printing it whole on a shared screen."""
    email = (email or "").strip()
    if "@" not in email:
        return email[:12]
    name, _, domain = email.partition("@")
    head = name[:2] if len(name) > 2 else name[:1]
    return f"{head}...@{domain}"


def build_selfservice_blueprint(mu) -> Blueprint:
    """The player-facing half of the multi-user layer, mounted at the root."""
    bp = Blueprint("multiuser_selfservice", __name__, template_folder=TEMPLATE_DIR)
    config = mu.config
    login_url = "/login/"
    account_url = "/account"
    # A code with no page named in it is a code nobody can find, so every email
    # says where it is typed.
    verify_where = f"the confirm-code page on {config.app_name}"
    reset_where = f"the reset-password page on {config.app_name}"
    account_where = f"the account page of your {config.app_name} account"

    # ------------------------------------------------------------- addressing
    def _ip() -> str:
        return netids.client_ip(request)

    def _ua() -> str:
        return netids.user_agent(request)

    # ------------------------------------------------------------ csrf token
    def csrf_token() -> str:
        token = session.get(CSRF_KEY)
        if not token:
            token = secrets.token_urlsafe(32)
            session[CSRF_KEY] = token
        return token

    def csrf_valid() -> bool:
        expected = session.get(CSRF_KEY) or ""
        supplied = request.form.get("_csrf") or request.headers.get("X-CSRF-Token") or ""
        return bool(expected) and hmac.compare_digest(str(expected), str(supplied))

    def rotate_csrf() -> None:
        session[CSRF_KEY] = secrets.token_urlsafe(32)

    # --------------------------------------------------------------- identity
    def identity() -> tuple[dict | None, dict | None]:
        """``(session row, user row)`` for this browser's account cookie."""
        if hasattr(g, "ss_identity"):
            return g.ss_identity
        pair: tuple[dict | None, dict | None] = (None, None)
        raw = session.get("mu_sid") or ""
        if raw:
            try:
                found = mu.accounts.validate_session(raw)
            except Exception:  # noqa: BLE001 - a broken layer must sign nobody in
                if mu.logger:
                    mu.logger.exception("[multiuser] self-service session validation failed")
                found = (None, None)
            if found and found[0] and found[1]:
                pair = (found[0], found[1])
        g.ss_identity = pair
        return pair

    def current_user() -> dict | None:
        sess, user = identity()
        if not user or user.get("status") != "active":
            return None
        g.ss_session = sess
        g.ss_user = user
        return user

    def account_required(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            if current_user() is None:
                return _json_error(
                    "web_session_required", "Sign in to reach your account page.", 401
                )
            return view(*args, **kwargs)

        return wrapper

    # ------------------------------------------------------------------ errors
    def _json_error(code, message, status, *, retryable=False, retry_after=0, fields=None):
        error = {"code": code, "message": message, "retryable": bool(retryable)}
        if fields:
            error["fields"] = fields
        response = jsonify({"error": error})
        if retry_after:
            response.headers["Retry-After"] = str(int(retry_after))
        return response, status

    def _result_page(
        heading,
        message,
        *,
        status=200,
        action_url=login_url,
        action_label="Sign in",
        secondary_url="",
        secondary_label="",
        retry_after=0,
    ):
        response = make_response(
            render_template(
                "selfservice_result.html",
                heading=heading,
                message=message,
                action_url=action_url,
                action_label=action_label,
                secondary_url=secondary_url,
                secondary_label=secondary_label,
            ),
            status,
        )
        # A browser that is told to slow down needs the number in the header, not
        # only in prose: fetch() reads the header, a form post reads the sentence.
        if retry_after:
            response.headers["Retry-After"] = str(int(retry_after))
        return response

    def _throttle(scope: str, key: str) -> tuple[bool, int]:
        limit, window = _LIMITS[scope]
        return mu.rate_limiter.allow(
            scope, key, limit, config.login_window_seconds if window is None else window
        )

    def _limited(scope: str, key: str, *, html: bool = False):
        allowed, retry_after = _throttle(scope, key)
        if allowed:
            return None
        message = f"Too many attempts. Try again in {retry_after} seconds."
        if html:
            return _result_page(
                "Slow down",
                message,
                status=429,
                action_url=request.path,
                action_label="Try again",
                retry_after=retry_after,
            )
        return _json_error("rate_limited", message, 429, retryable=True, retry_after=retry_after)

    def _error_for(exc: AccountError, *, status: int = 422, field: str = ""):
        message = str(exc)
        if message == USERNAME_TAKEN:
            return _json_error("username_taken", message, 409, fields={"username": message})
        if message == EMAIL_TAKEN:
            return _json_error("email_taken", EMAIL_TAKEN_GENERIC, 409)
        return _json_error("invalid", message, status, fields={field: message} if field else None)

    # ----------------------------------------------------------- mail plumbing
    def _send_code(user: dict, purpose: str, ip: str, user_agent: str, where: str):
        """Issue and email a typed code for this account. Returns the Mailer result.

        No URL is built or returned anywhere on this path: the message is the code
        and the name of the page to type it into, and nothing in it is clickable.
        """
        code = mu.accounts.create_email_code(
            user["id"], purpose, user.get("email") or "", ip=ip, user_agent=user_agent
        )
        return emails.send_code_email(
            mu, user, purpose, code, where=where, ip=ip, user_agent=user_agent
        )

    def _mail_note(result) -> str:
        """What the door tells the visitor about delivery.

        An outbox write means this server cannot send mail at all, and the page
        would be lying about a code nobody receives. An SMTP failure stays vague:
        the reason belongs in the administrator's audit log, not on a stranger's
        screen."""
        if result.status == "sent":
            return ""
        if result.status == "outbox":
            return (
                "This server is not configured to send email yet, so the code was "
                "written to its outbox instead. Ask the administrator to confirm "
                "your address."
            )
        return "The email could not be sent. Try again, or ask the administrator."

    def _honeypot_filled() -> bool:
        """True when the off-screen field carries a value: a script, not a person.

        The mail endpoints answer with the same page either way, so a bot learns
        nothing about which addresses exist and sends nothing."""
        return bool((request.form.get("website") or "").strip())

    # ------------------------------------------------------------- the guard
    @bp.before_request
    def _guard():
        if (
            request.method in {"POST", "PUT", "PATCH", "DELETE"}
            and request.path.startswith("/api/account")
            and current_user() is None
        ):
            # Identity is answered before the token is examined. A stranger with no
            # session gets one flat "sign in" 401 whether or not its CSRF header was
            # plausible, and a cross-site form from a *signed-in* browser is still
            # caught by the check below.
            return _json_error(
                "web_session_required", "Sign in to reach your account page.", 401
            )
        if request.method in {"POST", "PUT", "PATCH", "DELETE"} and not csrf_valid():
            if request.path.startswith("/api/"):
                return _json_error(
                    "csrf_failed", "Your page token is stale. Reload the page and try again.", 400
                )
            return _result_page(
                "Form expired",
                "This form had expired before you submitted it, so nothing was changed. "
                "Open it again and try once more.",
                status=400,
            )
        if any(
            request.path == path or request.path.startswith(path + "/")
            for path in _SESSION_SURFACE
        ):
            # The administrator door's per-browser budget, shared: one visitor does
            # not get 180 requests a minute at each door.
            allowed, retry_after = mu.rate_limiter.allow(
                "admin-request", netids.ip_bucket(_ip()), config.request_max_per_minute, 60
            )
            if not allowed:
                message = f"Too many requests. Try again in {retry_after} seconds."
                if request.path.startswith("/api/"):
                    return _json_error(
                        "rate_limited", message, 429, retryable=True, retry_after=retry_after
                    )
                return _result_page("Slow down", message, status=429, retry_after=retry_after)
        return None

    @bp.after_request
    def _security_headers(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        # These pages carry no inline script and no inline style, so the policy is
        # strict from the start instead of loosened later to fit a template.
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self'; script-src 'self'; "
            "img-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'self'"
        )
        return response

    @bp.context_processor
    def _context():
        policy = mu.accounts.signup_policy()
        return {
            "csrf_form": csrf_token(),
            "app_name": config.app_name,
            "password_min_length": PASSWORD_MIN_LENGTH,
            "login_url": login_url,
            "signup_enabled": config.self_signup_enabled,
            "signup_open": policy["open"],
            "signup_reason": policy["reason"],
            "code_minutes": config.email_code_minutes,
            "code_attempts": config.email_code_attempts,
        }

    @bp.app_template_global("asset_url")
    def _static_url(relative: str) -> str:
        return f"/static/{relative}?v={_asset_version(relative)}"

    @bp.app_context_processor
    def _availability():
        """Visible to *every* template, including the player remote that server.py
        renders. Its profile menu asks one question: does this browser have an
        account door to open? A Jam guest, or a single-owner install with the layer
        switched off, gets nothing rendered at all -- never a link to a 404."""
        return {"account_available": current_user() is not None}

    # ====================================================================
    #                          creating an account
    # ====================================================================

    def _signups_closed(reason: str = ""):
        """The closed-door page, saying which kind of closed this is.

        Three different sentences for three different situations, because the
        advice differs: a cap that resets tomorrow invites the visitor back, an
        administrator's switch means "ask us", and a deployment with no self-signup
        at all means neither. Telling somebody to wait for a door that will never
        open is how support mail gets written.
        """
        if reason == "daily_cap":
            message = (
                f"This Music Box has already accepted {config.max_self_signups_per_day} "
                "self-created accounts in the last day. Try again tomorrow, or ask an "
                "administrator to make one for you now."
            )
        elif reason == "administrator":
            message = (
                "An administrator has switched public sign-ups off on this Music Box. If "
                "you were meant to have an account, ask them to create one for you -- and "
                "they can switch public sign-ups back on whenever they like."
            )
        else:
            message = (
                "This Music Box does not let people create their own accounts at all. "
                "Ask an administrator to create one for you."
            )
        return _result_page(
            "Sign-ups are closed", message, status=503, action_label="Sign in"
        )

    def _issue_signup_nonce() -> None:
        session[SIGNUP_NONCE_KEY] = (secrets.token_urlsafe(16), time.time())

    def _signup_nonce_state() -> tuple[bool, bool]:
        """``(form was opened here, filled faster than a person can type)``.

        A missing nonce is a reload or a script that skipped the GET; the second
        case is a bot, and gets an answer that tells it nothing."""
        nonce = session.pop(SIGNUP_NONCE_KEY, None)
        if not nonce:
            return False, False
        try:
            issued_at = float(nonce[1])
        except (TypeError, IndexError, ValueError):
            return False, False
        return True, (time.time() - issued_at) < max(0, config.signup_min_seconds)

    def _fake_signup_success(username: str, email: str, reason: str, ip: str, user_agent: str):
        """Answer a scripted sign-up exactly like a real one.

        Telling a bot which rule it tripped is a specification it can satisfy, and
        the address it typed never comes back. No account exists afterwards."""
        domain = (email.rsplit("@", 1)[-1] or "")[:80]
        mu.accounts.audit(
            None, "signup_" + reason, None,
            {"username": username[:60], "email_domain": domain}, ip, user_agent,
        )
        return _result_page(
            "Check your inbox",
            f"If those details are usable, a six-digit verification code is on its way "
            f"to {_mask(email)}. It expires after {config.email_code_minutes} minutes.",
            secondary_url="/resend-code",
            secondary_label="Did not arrive? Send it again",
        )

    def _signup_form(form=None, field_errors=None, form_error="", status=200):
        _issue_signup_nonce()
        return (
            render_template(
                "signup.html",
                form=form or {},
                field_errors=field_errors or None,
                form_error=form_error,
            ),
            status,
        )

    @bp.route("/signup", methods=["GET", "POST"])
    def signup():
        if request.method == "GET":
            policy = mu.accounts.signup_policy()
            if not policy["open"]:
                return _signups_closed(policy["reason"])
            return _signup_form()

        ip, user_agent = _ip(), _ua()
        # The door state is answered before anything a visitor could fix (honeypot,
        # form nonce, rate limit): "come back tomorrow" is not a message to bury
        # behind "your form expired, reload it".
        policy = mu.accounts.signup_policy()
        if not policy["open"]:
            return _signups_closed(policy["reason"])
        username = (request.form.get("username") or "").strip()
        email = (request.form.get("email") or "").strip()
        # Off-screen honeypot: a human cannot see or tab into this field.
        if _honeypot_filled():
            return _fake_signup_success(username, email, "honeypot", ip, user_agent)
        opened, too_fast = _signup_nonce_state()
        if not opened:
            # No nonce means the form was never opened here, or its page was
            # reloaded and resubmitted. That is honest feedback, not a bot tell.
            return _signup_form(
                {"username": username, "email": email},
                {"username": "Open the sign-up form again before submitting it."},
                status=400,
            )
        if too_fast:
            return _fake_signup_success(username, email, "too_fast", ip, user_agent)

        blocked = _limited("signup-ip", netids.ip_bucket(ip), html=True)
        if blocked:
            return blocked
        blocked = _limited("signup-id", netids.identifier_bucket(email), html=True)
        if blocked:
            return blocked

        password = request.form.get("password") or ""
        confirm = request.form.get("confirm_password") or ""
        errors: dict[str, str] = {}
        clean_username = clean_email = ""
        try:
            clean_username = AccountService.validate_username(username)
            clean_email = AccountService.validate_email(email)
        except AccountError as exc:
            message = str(exc)
            errors["username" if message.startswith("Username") or message.startswith("That username") else "email"] = message
        if not errors.get("email") and not clean_email:
            errors["email"] = (
                "An email address is required, so you can reset your password later."
            )
        if password != confirm:
            errors["confirm_password"] = "The two passwords do not match."
        else:
            problem = validate_password(password, clean_username)
            if problem:
                errors["password"] = problem
        if errors:
            return _signup_form(
                {"username": username, "email": email}, errors, status=422
            )

        try:
            user = mu.accounts.register_self_service(
                username=clean_username,
                password=password,
                email=clean_email,
                ip=ip,
                user_agent=user_agent,
            )
        except AccountError as exc:
            message = str(exc)
            if message == EMAIL_TAKEN:
                return _signup_form(
                    {"username": clean_username, "email": clean_email},
                    form_error=EMAIL_TAKEN_GENERIC,
                    status=409,
                )
            if message == USERNAME_TAKEN:
                return _signup_form(
                    {"email": clean_email}, {"username": message}, status=409
                )
            if message.startswith("Sign-up is not available"):
                return _signups_closed(mu.accounts.signup_policy()["reason"])
            return _signup_form(
                {"username": clean_username, "email": clean_email}, form_error=message,
                status=422,
            )

        rotate_csrf()
        note = _mail_note(_send_code(user, "verify", ip, user_agent, verify_where))
        if note:
            return _result_page("Almost there", note, action_label="Back to sign in")
        # Straight to the code box, with this browser told which account it just
        # asked about.  A person should not have to find a second page and retype a
        # username they were given a field for thirty seconds ago.
        session[PENDING_VERIFY_KEY] = [user["id"], user.get("username") or ""]
        return redirect("/verify")

    @bp.route("/verify", methods=["GET", "POST"])
    def verify_signup():
        """Type the six digits from the email.  There is no link to open.

        Deliberately **not** behind the sign-up policy: closing the door stops new
        accounts being created, it must not strand somebody who was mid-way through
        confirming one when it closed.  Nothing on this page can create anything.
        """
        ip, user_agent = _ip(), _ua()
        pending_uid, pending_username = (session.get(PENDING_VERIFY_KEY) or ("", ""))[:2]
        typed = (request.form.get("username") or "").strip() if request.method == "POST" else ""
        # A browser that just signed up is told which account to confirm; anybody who
        # arrives cold types a username.  Neither is trusted for more than the code
        # proves, and neither answer says which of the two happened.
        if typed:
            user = mu.accounts.get_user_by_username(typed)
        elif pending_uid:
            user = mu.accounts.get_user(pending_uid)
        else:
            user = None
        username = (typed or pending_username) if user else ""

        def page(errors=None, form_error="", status=200):
            return (
                render_template(
                    "signup_verify.html",
                    username=username,
                    masked_email=_mask((user or {}).get("email") or ""),
                    field_errors=errors or None,
                    form_error=form_error,
                ),
                status,
            )

        dead_code = (
            "That code did not work. Codes are single-use, expire after "
            f"{config.email_code_minutes} minutes and stop working after "
            f"{config.email_code_attempts} wrong tries. Ask for a new one below."
        )
        if request.method == "GET":
            if user and not mu.accounts.email_code_state(user["id"], "verify")["pending"]:
                return page(form_error=dead_code)
            return page()

        if not user:
            # "No such account" and "wrong code" are the same sentence here, because
            # the difference between them is an account enumerator's whole toolkit.
            return page({"code": dead_code} if (typed or pending_username) else
                        {"username": "Enter the username you signed up with."},
                        status=410)
        blocked = _limited("code-ip", netids.ip_bucket(ip), html=True)
        if blocked:
            return blocked
        blocked = _limited(
            "code-id", netids.identifier_bucket(user.get("username") or username), html=True
        )
        if blocked:
            return blocked
        consumed = mu.accounts.consume_email_code(
            user["id"], "verify", request.form.get("code") or ""
        )
        if not consumed:
            return page({"code": dead_code}, status=410)
        mu.accounts.verify_email_with_token(user["id"], ip=ip, user_agent=user_agent)
        session.pop(PENDING_VERIFY_KEY, None)
        rotate_csrf()
        # No session is minted here.  A code proves a mailbox, not the password, and
        # this door has never opened without both.
        return _result_page(
            "Email confirmed",
            f"{_mask(user.get('email') or '')} is verified for account "
            f"@{user.get('username')}. Sign in and your account is live.",
            action_label="Sign in",
        )

    @bp.route("/resend-code", methods=["GET", "POST"])
    def resend_verification():
        spec = dict(
            page_title="Send a new code",
            intro=(
                "Enter the email address or username you signed up with. Only an "
                "address that still needs confirming gets a new code, and the old "
                "one stops working the moment the new one is sent."
            ),
            field_name="identifier",
            field_label="Email address or username",
            field_type="text",
            field_autocomplete="username",
            submit_label="Send a new code",
            back_url=login_url,
            back_label="Back to sign in",
            sent_link_url="/verify",
            sent_link_label="Enter the code",
        )
        if request.method == "GET":
            return render_template("selfservice_form.html", **spec)
        ip, user_agent = _ip(), _ua()
        identifier = (request.form.get("identifier") or "").strip()
        scripted = _honeypot_filled()
        blocked = _limited("resend-ip", netids.ip_bucket(ip), html=True)
        if blocked:
            return blocked
        blocked = _limited("resend-id", netids.identifier_bucket(identifier), html=True)
        if blocked:
            return blocked
        user = (
            mu.accounts.find_user_by_email(identifier)
            if "@" in identifier
            else mu.accounts.get_user_by_username(identifier)
        )
        sent_to = ""
        if (
            not scripted
            and user
            and mu.accounts.verification_pending(user)
            and mu.accounts.recent_email_token_count(user["id"], "verify", 60) == 0
        ):
            result = _send_code(user, "verify", ip, user_agent, verify_where)
            mu.accounts.audit(
                None, "verification_code_resent", user["id"],
                {"delivery": result.status}, ip, user_agent,
            )
            # This browser just proved it holds the mailbox *and* the username, so
            # remembering which account it is confirming is not a privilege gain --
            # and it saves a stranger from retyping what they typed a minute ago.
            session[PENDING_VERIFY_KEY] = [user["id"], user.get("username") or ""]
            sent_to = user
        elif scripted:
            mu.accounts.audit(
                None, "verification_resend_honeypot", None,
                {"identifier": identifier[:120]}, ip, user_agent,
            )
        elif identifier:
            mu.accounts.audit(
                None, "verification_resend_requested_unknown", None,
                {"identifier": identifier[:120]}, ip, user_agent,
            )
        # Same page either way: whether an address is registered here is not
        # information a stranger gets.
        return render_template(
            "selfservice_form.html",
            **spec,
            sent_note=(
                "If that account still needs confirming, a fresh six-digit code is on "
                f"its way. It expires after {config.email_code_minutes} minutes."
            ),
            username=sent_to.get("username") if sent_to else "",
        )

    # -------------------------------------------------------------- invitation
    @bp.route("/invite/<token>", methods=["GET", "POST"])
    def accept_invite(token):
        """The one emailed credential that is still a link, on the public door.

        An invitation carries an address an administrator vouched for and a password
        nobody has chosen yet, so it cannot be a typed code; it lives here rather
        than under /admin so a stranger's email says nothing about the panel behind
        it, and accepting one still never signs anybody in."""
        info = mu.accounts.peek_email_token(token, "invite")
        if not info:
            return _result_page(
                "This invitation is no longer valid",
                "Invitations expire after 72 hours and can only be used once. Ask an "
                "administrator to send a new one.",
                status=410,
                action_label="Go to sign in",
            )

        def page(error: str = "", status: int = 200):
            return (
                render_template(
                    "invite.html",
                    token=token,
                    email=info["email"],
                    username=info["username"],
                    form_error=error,
                ),
                status,
            )

        if request.method == "GET":
            return page()
        password = request.form.get("password") or ""
        confirm = request.form.get("confirm_password") or ""
        if password != confirm:
            return page("The two passwords do not match.", 400)
        consumed = mu.accounts.consume_email_token(token, "invite")
        if not consumed:
            return _result_page(
                "This invitation has already been used",
                "Each invitation works once. Sign in with the password you chose, or "
                "ask an administrator to send a new one.",
                status=410,
                action_label="Sign in",
            )
        try:
            mu.accounts.complete_invitation(
                consumed["user"]["id"], password, ip=_ip(), user_agent=_ua()
            )
        except AccountError as exc:
            return page(str(exc), 400)
        rotate_csrf()
        return _result_page(
            "Account activated",
            f"@{consumed['user'].get('username') or info['username']} is ready. Sign in "
            "with the password you just chose.",
            action_label="Sign in",
        )

    # --------------------------------------------------------- forgot / reset
    @bp.route("/forgot-password", methods=["GET", "POST"])
    def forgot_password():
        spec = dict(
            page_title="Reset your password",
            intro=(
                "Enter your email address or username. If an account matches, we send a "
                f"six-digit code to type into the next page; it expires after "
                f"{config.email_code_minutes} minutes, works once, and stops working "
                f"after {config.email_code_attempts} wrong tries."
            ),
            field_name="identifier",
            field_label="Email address or username",
            field_type="text",
            field_autocomplete="username",
            submit_label="Send reset code",
            back_url=login_url,
            back_label="Back to sign in",
            sent_link_url="/reset-password",
            sent_link_label="Enter the code",
        )
        if request.method == "GET":
            return render_template("selfservice_form.html", **spec)
        ip, user_agent = _ip(), _ua()
        identifier = (request.form.get("identifier") or "").strip()
        scripted = _honeypot_filled()
        # These bucket names are the administrator door's, so one attacker cannot
        # spend a separate budget on each way into the same password.
        blocked = _limited("forgot-ip", netids.ip_bucket(ip), html=True)
        if blocked:
            return blocked
        blocked = _limited("forgot-id", netids.identifier_bucket(identifier), html=True)
        if blocked:
            return blocked
        user = (
            mu.accounts.find_user_by_email(identifier)
            if "@" in identifier
            else mu.accounts.get_user_by_username(identifier)
        )
        if (
            not scripted
            and user
            and user.get("status") == "active"
            and user.get("email")
            and mu.accounts.recent_email_token_count(user["id"], "reset", 60) == 0
        ):
            result = _send_code(user, "reset", ip, user_agent, reset_where)
            mu.accounts.audit(
                None, "password_reset_code_requested", user["id"], {"delivery": result.status},
                ip, user_agent,
            )
            # Whose reset this is, remembered for the browser that just asked. It is
            # an identifier only -- the code is still what opens the door.
            session[PENDING_VERIFY_KEY] = [user["id"], user.get("username") or ""]
        elif scripted:
            mu.accounts.audit(
                None, "password_reset_honeypot", None,
                {"identifier": identifier[:120]}, ip, user_agent,
            )
        elif identifier:
            mu.accounts.audit(
                None, "password_reset_requested_unknown", None,
                {"identifier": identifier[:120]}, ip, user_agent,
            )
        # What the visitor reads must not depend on whether an account matched --
        # that difference is the whole enumeration trick, and the code above is
        # issued either way. It may only state a fact that is true of every
        # request to this deployment: can this server send email at all? The
        # per-account delivery outcome stays in the audit log.
        return render_template(
            "selfservice_form.html",
            **spec,
            sent_note=(
                "If an account matches that address, a reset code is on its way."
                if mu.mailer.config.configured
                else (
                    "This server is not configured to send email, so no code can arrive "
                    "yet. Ask the administrator to set it up."
                )
            ),
        )

    @bp.route("/reset-password", methods=["GET", "POST"])
    def reset_password():
        """Choose a new password by typing the emailed code. No link involved.

        A code alone is not enough to reset a password: the identifier says which
        account is being opened, so the two are checked together against one row
        that only exists for the account that was emailed.
        """
        ip, user_agent = _ip(), _ua()
        pending_uid, pending_username = (session.get(PENDING_VERIFY_KEY) or ("", ""))[:2]

        def page(form=None, field_errors=None, form_error="", status=200, username=""):
            return (
                render_template(
                    "reset_password.html",
                    form=form or {},
                    username=username,
                    field_errors=field_errors or None,
                    form_error=form_error,
                ),
                status,
            )

        if request.method == "GET":
            return page(username=pending_username)

        blocked = _limited("reset-ip", netids.ip_bucket(ip), html=True)
        if blocked:
            return blocked

        identifier = (request.form.get("identifier") or "").strip() or pending_username
        form = {
            "identifier": identifier,
            "code": request.form.get("code") or "",
            "confirm_password": request.form.get("confirm_password") or "",
        }
        user = (
            mu.accounts.find_user_by_email(identifier)
            if "@" in identifier
            else mu.accounts.get_user_by_username(identifier)
        ) if identifier else None
        if user and user.get("id") != pending_uid:
            # A code belongs to one account. Answering "wrong code" for somebody
            # else's username is the honest reply: it neither confirms the address
            # nor lets a second account's credential be spent here.
            user = None
        if not user:
            blocked = _limited(
                "code-id", netids.identifier_bucket(identifier), html=True
            )
            if blocked:
                return blocked
            return page(
                form,
                {"code": "That code did not work for that account. Codes are single-use "
                         f"and expire after {config.email_code_minutes} minutes."},
                status=410,
                username=identifier,
            )

        code = request.form.get("code") or ""
        password = request.form.get("password") or ""
        confirm = request.form.get("confirm_password") or ""
        errors: dict[str, str] = {}
        if password != confirm:
            errors["confirm_password"] = "The two passwords do not match."
        problem = validate_password(password, user.get("username") or "")
        if problem and not errors.get("confirm_password"):
            errors["password"] = problem
        if errors:
            return page(form, errors, status=422, username=identifier)

        blocked = _limited("code-ip", netids.ip_bucket(ip), html=True)
        if blocked:
            return blocked
        allowed, retry_after = _throttle(
            "code-id", netids.identifier_bucket(user.get("username") or "")
        )
        if not allowed:
            return page(
                form,
                {"code": f"Too many tries on that account. Wait {retry_after} seconds "
                         "and enter the code again."},
                status=429,
                username=identifier,
            )
        consumed = mu.accounts.consume_email_code(user["id"], "reset", code)
        if not consumed:
            return page(
                form,
                {"code": "That code did not work. Codes are single-use, expire after "
                         f"{config.email_code_minutes} minutes and stop working after "
                         f"{config.email_code_attempts} wrong tries. Ask for a new one."},
                status=410,
                username=identifier,
            )
        try:
            mu.accounts.reset_password_with_token(
                user["id"], password, ip=ip, user_agent=user_agent
            )
        except AccountError as exc:
            return page(None, None, str(exc), status=422, username=identifier)
        session.pop(PENDING_VERIFY_KEY, None)
        rotate_csrf()
        return _result_page(
            "Password changed",
            f"Sign in as @{user.get('username')} with the new password. "
            "Every other device was signed out.",
            action_label="Sign in",
        )

    # ====================================================================
    #                            the account page
    # ====================================================================

    def _human_stamp(seconds) -> str:
        """An absolute local time for server-rendered rows; "" when there is none."""
        try:
            value = int(seconds or 0)
        except (TypeError, ValueError):
            return ""
        if value <= 0:
            return ""
        try:
            return time.strftime("%b %d, %Y, %H:%M", time.localtime(value))
        except (ValueError, OSError, OverflowError):
            return ""

    @bp.route("/account", methods=["GET"])
    def account_page():
        if current_user() is None:
            return redirect(f"{login_url}?{urlencode({'next': account_url})}")
        # The page renders complete from the server, so it is readable before (or
        # without) account.js; the script only refreshes pieces after an action.
        return render_template("account.html", **_account_payload())

    def _theme_for(user: dict) -> str:
        """The palette this account is drawn in, dark when it never chose one.

        The choice itself is made by the administrator panel's theme control, which
        stores this same key against this same account: one preference per person, not
        one per surface, and no second switch on this page to disagree with the first.
        Read per render instead of kept in the session, so the choice follows the
        account to another browser.  An unreadable preference is not worth a 500.
        """
        try:
            stored = mu.accounts.get_preference(user["id"], THEME_KEY)
        except Exception:
            return "dark"
        return stored if stored in THEMES else "dark"

    def _account_payload() -> dict:
        full = mu.accounts.get_user(g.ss_user["id"]) or g.ss_user
        providers = mu.accounts.provider_status(full["id"])
        sessions = mu.accounts.session_summaries(
            full["id"], current_sid_hash=(g.ss_session or {}).get("sid_hash", "")
        )
        # This device first, then the most recently seen one; the page marks the
        # current row and never offers to sign it out with the others.
        sessions.sort(key=lambda row: (not row["current"], -(row["last_seen_at"] or 0)))
        for row in sessions:
            row["seen_text"] = _human_stamp(row.get("last_seen_at")) or "unknown"
            row["signed_in_text"] = _human_stamp(row.get("created_at")) or "unknown"
        return {
            "user": full,
            "username": full.get("username"),
            "email": full.get("email") or "",
            "email_verified": bool(full.get("email_verified")),
            "verification_pending": mu.accounts.verification_pending(full),
            # How many tries this code has left, so the page can say "two more"
            # rather than let somebody discover the ceiling by hitting it.
            "verification": mu.accounts.email_code_state(full["id"], "verify"),
            "role": full.get("role"),
            "role_text": "Administrator" if AccountService.can_view_admin(full) else "Player",
            "is_admin": AccountService.can_view_admin(full),
            "created_by": full.get("created_by"),
            "created_at": full.get("created_at"),
            "created_text": _human_stamp(full.get("created_at")),
            "must_change_password": bool(full.get("must_change_password")),
            "totp_enabled": mu.accounts.totp_is_enabled(full),
            "totp_pending": bool(full.get("totp_pending")),
            "providers": providers,
            "provider_map": {row["provider"]: row for row in providers},
            "sessions": sessions,
            "password_min_length": PASSWORD_MIN_LENGTH,
            "theme": _theme_for(full),
        }

    @bp.route("/api/account", methods=["GET"])
    @account_required
    def api_account():
        return jsonify(_account_payload())

    @bp.route("/api/account/providers/<provider>/disconnect", methods=["POST"])
    @account_required
    def api_disconnect_provider(provider):
        """Forget one linked service, secrets included.

        Deleting the database row alone leaves the cookies, the browser profile and
        the live session working, which is how "Disconnected" kept streaming -- so
        the answer distinguishes "removed everything" from "the row is gone but
        these artifacts are still here".
        """
        ip, user_agent = _ip(), _ua()
        try:
            failures, was_linked = mu.accounts.clear_own_provider_credentials(
                g.ss_user, provider, ip=ip, user_agent=user_agent
            )
        except AccountError as exc:
            return _json_error("invalid_provider", str(exc), 422)
        label = "YouTube Music" if provider == "youtube" else "Amazon"
        if failures:
            return jsonify({
                "ok": True,
                "complete": False,
                "message": (
                    f"{label} sign-in was removed, but this server could not delete "
                    f"everything yet ({'; '.join(failures)}). Try again in a minute; "
                    f"until then the old session may still work."
                ),
            })
        if not was_linked:
            # Nothing was connected, so saying a removal happened would be the same
            # kind of invented status this door used to report.
            return jsonify({
                "ok": True,
                "complete": True,
                "was_linked": False,
                "message": f"Nothing was connected to {label} on this account, so there "
                           "was nothing to remove.",
            })
        if provider == "youtube":
            message = (
                "YouTube Music sign-in was removed, and this server deleted the saved "
                "cookies and browser profile with it. This account plays through the "
                "server's session until you connect your own."
            )
        else:
            message = (
                "Amazon sign-in was removed, and this server deleted the saved Alexa "
                "cookies with it."
            )
        return jsonify({"ok": True, "complete": True, "message": message})

    @bp.route("/api/account/profile", methods=["POST"])
    @account_required
    def api_profile():
        ip, user_agent = _ip(), _ua()
        body = request.get_json(silent=True) or {}
        email = str(body.get("email") or "").strip()
        if not email:
            message = "An email address is required, so you can reset your password later."
            return _json_error("invalid", message, 422, fields={"email": message})
        before = mu.accounts.get_user(g.ss_user["id"]) or {}
        try:
            user = mu.accounts.update_own_profile(
                g.ss_user,
                email=email,
                current_password=str(body.get("current_password") or ""),
                ip=ip,
                user_agent=user_agent,
            )
        except AccountError as exc:
            message = str(exc)
            if "current password" in message.lower():
                return _json_error(
                    "bad_password", message, 403, fields={"current_password": message}
                )
            if message in (EMAIL_TAKEN, USERNAME_TAKEN):
                return _error_for(exc)
            return _json_error("invalid", message, 422, fields={"email": message})
        email_changed = (user.get("email") or "") != (before.get("email") or "")
        note = ""
        if email_changed:
            # The address is unverified from this moment, so the code that fixes it
            # has to be offered in the same breath as the change.
            note = _mail_note(_send_code(user, "verify", ip, user_agent, account_where))
        return jsonify(
            {
                "ok": True,
                "user": user,
                "email_changed": email_changed,
                "verification_pending": mu.accounts.verification_pending(user),
                "note": note,
            }
        )

    @bp.route("/api/account/password", methods=["POST"])
    @account_required
    def api_password():
        ip, user_agent = _ip(), _ua()
        blocked = _limited("account-password", g.ss_user["id"])
        if blocked:
            return blocked
        body = request.get_json(silent=True) or {}
        current_password = str(body.get("current_password") or "")
        new_password = str(body.get("password") or "")
        confirm = body.get("confirm_password")
        if confirm is not None and str(confirm) != new_password:
            message = "The two new passwords do not match."
            return _json_error("invalid", message, 422, fields={"confirm_password": message})
        problem = validate_password(new_password, g.ss_user.get("username") or "")
        if problem:
            return _json_error("invalid", problem, 422, fields={"password": problem})
        try:
            mu.accounts.change_own_password(
                g.ss_user,
                current_password,
                new_password,
                ip,
                user_agent,
                keep_sid_hash=session.get("mu_sid") or "",
            )
        except AccountError as exc:
            message = str(exc)
            if "current password" in message.lower():
                return _json_error(
                    "bad_password", message, 403, fields={"current_password": message}
                )
            return _json_error("invalid", message, 422)
        return jsonify(
            {
                "ok": True,
                "message": (
                    "Password changed. Other devices were signed out; this one stays "
                    "signed in. Turn two-factor on while you are here."
                ),
            }
        )

    @bp.route("/api/account/email/verification", methods=["POST"])
    @account_required
    def api_resend_verification():
        ip, user_agent = _ip(), _ua()
        user = mu.accounts.get_user(g.ss_user["id"]) or g.ss_user
        if not user.get("email"):
            return _json_error("no_email", "Add an email address first.", 422)
        if not mu.accounts.verification_pending(user):
            return jsonify({"ok": True, "note": "", "message": "This address is already confirmed."})
        blocked = _limited("resend-ip", netids.ip_bucket(ip))
        if blocked:
            return blocked
        blocked = _limited("resend-id", netids.identifier_bucket(user["email"]))
        if blocked:
            return blocked
        if mu.accounts.recent_email_token_count(user["id"], "verify", 60):
            return _json_error(
                "rate_limited", "A code was just sent. Check your inbox.", 429,
                retryable=True, retry_after=60,
            )
        note = _mail_note(_send_code(user, "verify", ip, user_agent, account_where))
        return jsonify({"ok": True, "note": note})

    @bp.route("/api/account/email/verification/confirm", methods=["POST"])
    @account_required
    def api_confirm_verification():
        """Type the six digits for the address this account already holds.

        No identifier in the body: the session says whose address it is, and a code
        is only ever valid against its own account's live row.
        """
        ip, user_agent = _ip(), _ua()
        user = mu.accounts.get_user(g.ss_user["id"]) or g.ss_user
        blocked = _limited("code-ip", netids.ip_bucket(ip))
        if blocked:
            return blocked
        allowed, retry_after = _throttle(
            "code-id", netids.identifier_bucket(user.get("username") or user["id"])
        )
        if not allowed:
            return _json_error(
                "rate_limited",
                f"Too many tries. Wait {retry_after} seconds and enter the code again.",
                429,
                retryable=True,
                retry_after=retry_after,
                fields={"code": "Wait before trying this code again."},
            )
        consumed = mu.accounts.consume_email_code(
            user["id"], "verify", str((request.get_json(silent=True) or {}).get("code") or "")
        )
        if not consumed:
            return _json_error(
                "invalid_code",
                "That code did not work. Codes are single-use, expire after "
                f"{config.email_code_minutes} minutes and stop working after "
                f"{config.email_code_attempts} wrong tries. Ask for a new one.",
                410,
                fields={"code": "Wrong, used, or expired. Send a new code and try once."},
            )
        verified = mu.accounts.verify_email_with_token(
            user["id"], ip=ip, user_agent=user_agent
        )
        return jsonify(
            {
                "ok": True,
                "user": verified or mu.accounts.get_user(user["id"]),
                "email_verified": True,
                "verification_pending": False,
                "message": f"{_mask(user.get('email') or '')} is confirmed.",
            }
        )

    @bp.route("/api/account/sessions/revoke", methods=["POST"])
    @account_required
    def api_revoke_sessions():
        body = request.get_json(silent=True) or {}
        only = str(body.get("id") or "").strip()
        everywhere = bool(body.get("all"))
        others = bool(body.get("others"))
        if not only and not everywhere and not others:
            return _json_error(
                "invalid", "Name a session, or sign out of every other device.", 422
            )
        try:
            revoked, killed_current = mu.accounts.revoke_own_sessions(
                g.ss_user,
                # Signing out everywhere means *everywhere*: a device list that
                # quietly kept this browser alive would be lying about the button.
                keep_sid_hash="" if everywhere else (session.get("mu_sid") or ""),
                only_prefix=only,
                ip=_ip(),
                user_agent=_ua(),
            )
        except AccountError as exc:
            return _json_error("invalid", str(exc), 422)
        if killed_current:
            _drop_account_cookies()
        return jsonify({"ok": True, "revoked": revoked, "signed_out": killed_current})

    def _drop_account_cookies() -> None:
        """Forget this account's credential in this browser, and nothing else.

        A Jam marker in the same cookie belongs to someone else's invite, so it
        stays; this is about the account being signed out, not the whole browser."""
        for key in ("mu_sid", "mu_user_id", CSRF_KEY, "mu_csrf", SIGNUP_NONCE_KEY):
            session.pop(key, None)

    # ------------------------------------------------------------------- 2FA
    @bp.route("/api/account/totp/begin", methods=["POST"])
    @account_required
    def api_totp_begin():
        ip, user_agent = _ip(), _ua()
        blocked = _limited("totp-begin", g.ss_user["id"])
        if blocked:
            return blocked
        body = request.get_json(silent=True) or {}
        try:
            enrollment = mu.accounts.begin_totp_enrollment(
                g.ss_user,
                current_password=str(body.get("current_password") or ""),
                issuer=config.app_name,
                ip=ip,
                user_agent=user_agent,
            )
        except AccountError as exc:
            message = str(exc)
            lowered = message.lower()
            if "incorrect" in lowered:
                return _json_error(
                    "bad_password", message, 403, fields={"current_password": message}
                )
            if "already on" in lowered:
                return _json_error("totp_already_enabled", message, 409)
            return _json_error("invalid", message, 422)
        return jsonify(
            {
                "ok": True,
                "secret": enrollment["secret"],
                "account": enrollment["account"],
                "issuer": enrollment["issuer"],
                "expires_at": enrollment["expires_at"],
                "qr_url": "/api/account/totp/qr",
                "message": "Scan this in your authenticator app, then type the code it shows.",
            }
        )

    @bp.route("/api/account/totp/qr", methods=["GET"])
    @account_required
    def api_totp_qr():
        """The provisioning URI as an image, for a phone camera.

        The URI *is* the secret, which is why it is never a JSON field: this
        endpoint's only purpose is to be the ``src`` of one same-origin ``<img>``
        on a no-store page."""
        enrollment = mu.accounts.pending_totp_enrollment(g.ss_user)
        if not enrollment:
            return _json_error("no_enrollment", "Start the two-factor setup again first.", 409)
        try:
            import io

            import qrcode
            import qrcode.image.svg
        except Exception:  # noqa: BLE001 - optional dependency: degraded, not broken
            if mu.logger:
                mu.logger.exception("[multiuser] qrcode package unavailable")
            return _json_error("qr_unavailable", "QR generation is unavailable.", 503)
        image = qrcode.make(enrollment["otpauth_uri"], image_factory=qrcode.image.svg.SvgImage)
        buffer = io.BytesIO()
        image.save(buffer)
        response = Response(buffer.getvalue(), mimetype="image/svg+xml")
        response.headers["Cache-Control"] = "no-store"
        return response

    @bp.route("/api/account/totp/confirm", methods=["POST"])
    @account_required
    def api_totp_confirm():
        blocked = _limited("totp-confirm", g.ss_user["id"])
        if blocked:
            return blocked
        body = request.get_json(silent=True) or {}
        try:
            user = mu.accounts.confirm_totp_enrollment(
                g.ss_user,
                code=str(body.get("code") or "").strip(),
                ip=_ip(),
                user_agent=_ua(),
            )
        except AccountError as exc:
            message = str(exc)
            bad_code = "did not match" in message.lower()
            return _json_error(
                "totp_code" if bad_code else "invalid",
                message,
                403 if bad_code else 422,
                fields={"code": message},
            )
        # No rotation here: the page stays open and keeps posting with the token
        # it was rendered with, and nothing about the session changed identity.
        return jsonify(
            {
                "ok": True,
                "totp_enabled": mu.accounts.totp_is_enabled(user),
                "message": "Two-factor sign-in is on. You will need a code next time.",
            }
        )

    @bp.route("/api/account/totp/cancel", methods=["POST"])
    @account_required
    def api_totp_cancel():
        try:
            mu.accounts.cancel_totp_enrollment(g.ss_user, ip=_ip(), user_agent=_ua())
        except AccountError as exc:
            return _json_error("invalid", str(exc), 422)
        return jsonify({"ok": True})

    @bp.route("/api/account/totp/disable", methods=["POST"])
    @account_required
    def api_totp_disable():
        blocked = _limited("totp-disable", g.ss_user["id"])
        if blocked:
            return blocked
        body = request.get_json(silent=True) or {}
        try:
            user = mu.accounts.disable_totp(
                g.ss_user,
                current_password=str(body.get("current_password") or ""),
                code=str(body.get("code") or "").strip(),
                ip=_ip(),
                user_agent=_ua(),
            )
        except AccountError as exc:
            message = str(exc)
            lowered = message.lower()
            if "incorrect" in lowered:
                return _json_error("bad_credentials", message, 403)
            return _json_error("invalid", message, 422)
        return jsonify(
            {
                "ok": True,
                "totp_enabled": mu.accounts.totp_is_enabled(user),
                "message": "Two-factor sign-in is off.",
            }
        )

    # -------------------------------------------------------------- self-delete
    @bp.route("/api/account/delete", methods=["POST"])
    @account_required
    def api_delete_account():
        ip, user_agent = _ip(), _ua()
        blocked = _limited("account-delete", g.ss_user["id"])
        if blocked:
            return blocked
        body = request.get_json(silent=True) or {}
        username = g.ss_user.get("username") or ""
        password = str(body.get("password") or "")
        confirm_username = str(body.get("confirm_username") or "")
        if password and password == confirm_username:
            # Typing the same string into both boxes proves nothing; refuse it
            # before it costs a password hash and a cascade of deletes.
            message = "Type your username in the confirmation box, not your password."
            return _json_error(
                "username_mismatch", message, 409, fields={"confirm_username": message}
            )
        try:
            mu.accounts.delete_own_account(
                g.ss_user,
                password=password,
                confirm_username=confirm_username,
                ip=ip,
                user_agent=user_agent,
            )
        except AccountError as exc:
            message = str(exc)
            lowered = message.lower()
            if "incorrect" in lowered:
                return _json_error("bad_password", message, 403, fields={"password": message})
            if "username exactly" in lowered:
                return _json_error(
                    "username_mismatch", message, 409, fields={"confirm_username": message}
                )
            # Either wording of "you are the last one who can run this server":
            # the delete path's own sentence, or the shared last-owner guard's.
            if "administrator" in lowered or "must remain" in lowered:
                return _json_error("last_owner", message, 409)
            return _json_error("invalid", message, 422)
        _drop_account_cookies()
        return jsonify(
            {
                "ok": True,
                "message": f"Account @{username} and everything on it is deleted.",
                "next": login_url,
            }
        )

    return bp
