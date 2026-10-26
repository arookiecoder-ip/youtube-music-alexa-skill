"""Automated Google sign-in for the server's own YouTube Music account.

The app has always been able to *hold* a YouTube Music session: a human opens the
noVNC desktop, types their password, and the sidecar captures the signed-in
headers. That stops working the moment Google decides the session is stale --
which it does, on a schedule nobody controls -- and every account that never
linked its own YouTube then loses Home, library and Liked at once.

This module closes that loop. It owns the *decision* half (what the configured
credential is, which answer to give next, when to stop trying, when to tell a
human) while the browser sidecar owns the *clicking* half. The split matters for
secrets: the sidecar container carries no Google password in its environment, so
nothing here leaks into a volume wipe or an image layer. It receives the
credential per step, over the loopback control API, and forgets it.

Nothing in this file logs, returns or audits a credential, a code or a cookie.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import re
import struct
import threading
import time

logger = logging.getLogger(__name__)

ENV_EMAIL = "YOUTUBE_LOGIN_USERNAME"
ENV_PASSWORD = "YOUTUBE_LOGIN_PASSWORD"
ENV_TOTP = "YOUTUBE_LOGIN_TOTP_CODE"
ENV_ALERT_TO = "LOGIN_FAIL_MAIL_ID"
ENV_ENABLED = "YOUTUBE_AUTO_LOGIN_ENABLED"

SECRET_MODE = "secret"
CODES_MODE = "codes"
NONE_MODE = "none"

_BUSY = "busy"
_NO_ANSWER = "no_answer"
_NOT_CONFIGURED = "not_configured"
_SIDECAR_FAILED = "sidecar_failed"

# Outcomes that end a run without a session and that a human can still rescue by
# hand. Anything in NEEDS_HUMAN on the sidecar side is in here too, plus the
# failures that are ours rather than Google's.
HUMAN_OUTCOMES = frozenset({
    "need_identifier", "need_choice", "need_prompt", "need_phone", "need_passkey",
    "captcha", "blocked", "consent", "wrong_password", "unknown_account", "locked",
    "unsupported", "timeout", _NO_ANSWER, _SIDECAR_FAILED, _BUSY,
})
# Retrying these without changing anything has a real chance of working: a code
# that missed its time window, a page that was still loading. A refused code is
# not in here because the ladder never terminates on one -- it answers it with the
# next window instead, so the run that ends is the one that ran out of windows.
TRANSIENT_OUTCOMES = frozenset({"timeout", _SIDECAR_FAILED})
# Not outages: nothing broke, this server was simply never configured to do this,
# or two callers asked at once.
_NEVER_ALERT = frozenset({_BUSY, _NOT_CONFIGURED})

_BASE32_RE = re.compile(r"^[A-Z2-7]+$")
_BACKUP_CODE_RE = re.compile(r"^[0-9a-z]{8}$")
_CODE_RE = re.compile(r"^[A-Za-z0-9_-]{6,16}$")

# The page states that mean "a 2-step answer goes here". Google's own refusal is
# one of them: it keeps the challenge open and asks again, which is a retry
# signal, not an end.
CODE_CHALLENGES = ("need_totp", "need_backup_code", "bad_code")


def totp_at(secret_b32: str, counter: int) -> str:
    """RFC 6238, matching ``multiuser.security.totp_at``.

    Re-implemented rather than imported because this module is dependency-light
    on purpose: the multiuser package pulls in Flask, and the auto-login layer
    has to work on a server where multi-user accounts are switched off entirely.
    """
    if counter < 0:
        # A clock before 1970 has no code to give. Raised as a bad value rather
        # than letting struct.pack throw something the caller cannot name, because
        # the caller's only sane response is "I have no answer for this".
        raise ValueError("TOTP counter must not be negative")
    padded = secret_b32 + "=" * (-len(secret_b32) % 8)
    key = base64.b32decode(padded, casefold=True)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = (int.from_bytes(digest[offset:offset + 4], "big") & 0x7FFFFFFF) % 1_000_000
    return f"{code:06d}"


def parse_credential(value: str) -> tuple[str, object]:
    """Decide what the configured 2FA value actually is.

    An operator pastes one of two things into that variable: the shared secret
    from the authenticator-setup page -- 16 or 32 characters of ``A-Z2-7``, often
    spaced in groups of four -- or a list of single-use backup codes, which are
    eight lowercase alphanumerics each. A secret generates codes forever; a
    backup code is gone once Google has accepted it, so the difference decides
    whether this feature is a keep-alive or a countdown.
    """
    raw = (value or "").strip()
    if not raw:
        return NONE_MODE, None
    tokens = [token for token in re.split(r"[\s,;]+", raw) if token]
    # Google writes backup codes as eight lowercase characters each and secrets as
    # one long base32 run, but a pair of codes such as "abcdefgh ijklmnop" is also
    # valid base32 once the spaces go. Deciding the list shape first is what keeps
    # a code list from being read as a secret that could never generate a code.
    if len(tokens) > 1 and all(_BACKUP_CODE_RE.match(token) for token in tokens):
        return CODES_MODE, tokens
    squashed = re.sub(r"[\s-]", "", raw).upper()
    if (len(squashed) >= 16 and len(squashed) % 8 == 0 and _BASE32_RE.match(squashed)):
        return SECRET_MODE, squashed
    codes = [token for token in tokens if _CODE_RE.match(token)]
    if codes and len(codes) == len(tokens):
        return CODES_MODE, codes
    # Too short to be a secret and not a clean code list: treat the whole value as
    # one code rather than refuse to answer a challenge we could still pass.
    if len(tokens) == 1 and _CODE_RE.match(tokens[0]):
        return CODES_MODE, tokens
    return NONE_MODE, None


class AnswerSupply:
    """The next thing to type into a code challenge, and what it cost to ask.

    A TOTP secret is unlimited, so the only judgement is how much clock skew to
    forgive: each answer moves one window further from the current time. Backup
    codes are not, so each one is offered exactly once and then dropped, accepted
    or refused -- a code Google has already said no to will not change its mind
    because it was typed again.
    """

    # Current window first, then the neighbour on each side: a server whose clock
    # drifted by thirty seconds should not need a human.
    WINDOWS = (0, -1, 1, -2, 2)

    def __init__(self, mode, secret="", codes=(), clock=time.time, on_spend=None):
        self.mode = mode
        self._secret = secret or ""
        self._codes = list(codes or [])
        self._window = 0
        self._clock = clock
        self._on_spend = on_spend

    @property
    def remaining(self):
        if self.mode == SECRET_MODE:
            return max(0, len(self.WINDOWS) - self._window)
        return len(self._codes)

    def next(self) -> str:
        if self.mode == SECRET_MODE:
            if self._window >= len(self.WINDOWS):
                return ""
            counter = int(self._clock()) // 30 + self.WINDOWS[self._window]
            self._window += 1
            try:
                return totp_at(self._secret, counter)
            except (ValueError, TypeError):
                return ""
        if self.mode == CODES_MODE and self._codes:
            # Not popped here: the caller pops it once Google has answered, so a
            # challenge that was never reached does not spend a code.
            return self._codes[0]
        return ""

    def spent(self) -> None:
        """Mark the code just offered as used up."""
        if self.mode == CODES_MODE and self._codes:
            burned = self._codes.pop(0)
            if self._on_spend is not None:
                self._on_spend(burned)

    def describe(self) -> str:
        if self.mode == SECRET_MODE:
            return "authenticator secret"
        if self.mode == CODES_MODE:
            return f"{len(self._codes)} backup code(s) left"
        return "no 2-step credential"


class ServerAccount:
    """The Google account this deployment signs in as, from the environment."""

    def __init__(self, email="", password="", credential="", clock=time.time):
        self.email = (email or "").strip()
        self.password = password or ""
        self.mode, value = parse_credential(credential)
        self.secret = value if self.mode == SECRET_MODE else ""
        self.codes = list(value) if self.mode == CODES_MODE else []
        self._clock = clock
        self._codes_lock = threading.Lock()

    @classmethod
    def from_env(cls, environ=None, clock=time.time):
        env = os.environ if environ is None else environ
        return cls(env.get(ENV_EMAIL, ""), env.get(ENV_PASSWORD, ""),
                   env.get(ENV_TOTP, ""), clock=clock)

    @property
    def configured(self) -> bool:
        return bool(self.email and self.password and self.mode != NONE_MODE)

    def supply(self) -> AnswerSupply:
        """A fresh supply for one run, reporting each accepted code back to the account.

        The countdown has to outlive the run that spent the code: a backup code
        Google has accepted is gone for everyone, so the next attempt must not
        offer it again and read its rejection as a brand-new problem.
        """
        return AnswerSupply(self.mode, self.secret, self.codes, clock=self._clock,
                            on_spend=self._burn)

    def _burn(self, code) -> None:
        with self._codes_lock:
            try:
                self.codes.remove(code)
            except ValueError:
                pass

    def describe(self) -> dict:
        """Safe to render in an admin panel: shape only, never a value."""
        with self._codes_lock:
            remaining = len(self.codes)
        return {
            "configured": self.configured,
            "email": self.email,
            "mode": self.mode,
            "credential": AnswerSupply(self.mode, self.secret, self.codes).describe(),
            "codes_remaining": remaining if self.mode == CODES_MODE else None,
        }


class SignInLog:
    """What the last few attempts did, for the panel and for the alert throttle."""

    def __init__(self, clock=time.time):
        self._clock = clock
        self._lock = threading.Lock()
        self.last_attempt = 0.0
        self.last_success = 0.0
        self.last_outcome = ""
        self.consecutive_failures = 0
        self.next_attempt_at = 0.0
        self.alerted_at = 0.0
        self.alerted_outcome = ""
        self.attempts = 0

    def record(self, outcome, ok):
        with self._lock:
            now = self._clock()
            self.last_attempt = now
            self.attempts += 1
            self.last_outcome = outcome
            if ok:
                self.last_success = now
                self.consecutive_failures = 0
                self.next_attempt_at = 0.0
                # A recovery resets the throttle, so the next outage is reported
                # even if it is the same outage as last month.
                self.alerted_outcome = ""
            else:
                self.consecutive_failures += 1
        return self.snapshot()

    def schedule_retry(self, delay):
        with self._lock:
            self.next_attempt_at = self._clock() + max(0.0, delay)

    def due(self):
        with self._lock:
            return self._clock() >= self.next_attempt_at

    def mark_alerted(self, outcome):
        with self._lock:
            self.alerted_outcome = outcome
            self.alerted_at = self._clock()
            return self.alerted_at

    def alert_state(self):
        with self._lock:
            return self.alerted_outcome, self.alerted_at

    def snapshot(self):
        with self._lock:
            return {
                "last_attempt": self.last_attempt or None,
                "last_success": self.last_success or None,
                "last_outcome": self.last_outcome,
                "consecutive_failures": self.consecutive_failures,
                "next_attempt_at": self.next_attempt_at or None,
                "attempts": self.attempts,
                "alerted_at": self.alerted_at or None,
            }


class GoogleAutoLogin:
    """Walk the sidecar through Google's form until it yields a session or a wall.

    ``controller`` is the sidecar client (anything with ``call(path, method,
    payload)``), and ``on_session(headers, cookies)`` is what to do with a
    captured login -- writing the auth JSON and the download-cookie jar is the
    caller's business, because only the caller knows whose files these are.
    """

    # A page that never resolves is a dead end, not a puzzle to keep at: every
    # failed click on Google's form is a data point for their abuse model.
    max_steps = 12

    def __init__(self, controller, account=None, on_session=None, alert=None,
                 clock=time.time, sleep=time.sleep, log=None,
                 backoff_base=900.0, backoff_cap=21600.0, alert_cooldown=21600.0,
                 enabled=True):
        self.controller = controller
        self.account = account or ServerAccount()
        self.on_session = on_session
        self.alert = alert
        self.clock = clock
        self.sleep = sleep
        self.log = log or SignInLog(clock=clock)
        self.backoff_base = float(backoff_base)
        self.backoff_cap = float(backoff_cap)
        self.alert_cooldown = float(alert_cooldown)
        self.enabled = bool(enabled)
        self._run_lock = threading.Lock()

    @classmethod
    def from_env(cls, controller, on_session=None, alert=None, **kwargs):
        return cls(controller, ServerAccount.from_env(clock=kwargs.get("clock", time.time)),
                   on_session=on_session, alert=alert,
                   enabled=os.environ.get(ENV_ENABLED, "1").strip().lower()
                   not in {"0", "false", "no", "off"}, **kwargs)

    # -- the ladder --------------------------------------------------------

    def sign_in(self, profile_key="", reason="scheduled"):
        """One full attempt. Returns ``{outcome, ok, detail}`` and never raises."""
        if not self.enabled:
            return self._finish(_NOT_CONFIGURED, False, "Automatic sign-in is switched off.")
        if not self.account.configured:
            return self._finish(_NOT_CONFIGURED, False,
                                "No Google account is configured for this server.")
        if not self._run_lock.acquire(blocking=False):
            return self._outcome(_BUSY, False, "Another sign-in is already running.")
        try:
            result = self._run(profile_key or "", reason)
        except Exception as exc:
            # A sidecar that is down, a Playwright timeout, a malformed answer: all
            # of them end here, because the caller is a background thread that has
            # nothing to do with an exception but report it.
            logger.warning("Automatic YouTube sign-in failed (%s)", type(exc).__name__)
            result = self._outcome(_SIDECAR_FAILED, False,
                                   "The browser service could not complete the sign-in.")
        finally:
            self._stop_quietly()
            self._run_lock.release()
        return self._finish(result["outcome"], result["ok"], result.get("detail", ""),
                            reason=reason)

    def _run(self, profile_key, reason):
        supply = self.account.supply()
        asked_for_identifier = False
        outcome, state = self._call("/google/start", {"profile": profile_key,
                                                      "email": self.account.email})
        steps = 0
        while steps < self.max_steps:
            steps += 1
            if outcome == "signed_in":
                return self._capture(profile_key)
            if outcome == "need_identifier":
                if asked_for_identifier:
                    # The address was typed once and Google asked for it again. That
                    # is a refusal, not a reload: repeating it only hands their abuse
                    # model a bot that cannot give up.
                    return self._outcome("need_identifier", False, describe_outcome("need_identifier"))
                asked_for_identifier = True
                outcome, state = self._call("/google/identifier",
                                            {"profile": profile_key,
                                             "email": self.account.email})
                continue
            if outcome == "need_password":
                outcome, state = self._call("/google/password",
                                            {"profile": profile_key,
                                             "password": self.account.password})
                continue
            if outcome == "need_choice":
                outcome, state = self._call("/google/choice", {"profile": profile_key})
                continue
            if outcome == "consent":
                outcome, state = self._call("/google/consent", {"profile": profile_key})
                continue
            if outcome in CODE_CHALLENGES:
                answer = supply.next()
                if not answer:
                    return self._outcome(_NO_ANSWER, False,
                                         f"The 2-step credential is exhausted ({supply.describe()}).")
                outcome, state = self._call("/google/code", {"profile": profile_key,
                                                             "code": answer})
                # This answer is now finished with either way: Google accepted it, or
                # refused it and a refused code is never worth typing twice. A secret
                # moves to the next clock window; a backup code moves to the next code.
                supply.spent()
                continue
            detail = (state or {}).get("detail") or ""
            return self._outcome(outcome, False, detail or describe_outcome(outcome))
        return self._outcome("timeout", False, "Google kept changing the question.")

    def _capture(self, profile_key):
        """Turn a signed-in browser into the two artifacts the app needs."""
        outcome, state = self._call("/google/capture", {"profile": profile_key})
        if outcome != "captured":
            return self._outcome(outcome or "timeout", False,
                                 "Signed in, but the YouTube Music session could not be captured.")
        candidate = state or {}
        if self.on_session is not None:
            self.on_session(candidate.get("headers"), candidate.get("cookies") or "")
        return self._outcome("signed_in", True, "")

    def _call(self, path, payload):
        """One sidecar step. Returns ``(outcome, state)``; state may be empty."""
        response = self.controller.call(path, "POST", payload)
        if not isinstance(response, dict):
            return _SIDECAR_FAILED, {}
        return (response.get("outcome") or response.get("state") or _SIDECAR_FAILED,
                response)

    def _stop_quietly(self):
        try:
            self.controller.call("/google/stop", "POST", {})
        except Exception:
            pass

    # -- outcomes ----------------------------------------------------------

    def _finish(self, outcome, ok, detail, reason=""):
        result = self._outcome(outcome, ok, detail)
        self.log.record(outcome, ok)
        if ok:
            return result
        delay = min(self.backoff_cap, self.backoff_base * (2 ** max(0, self.log.snapshot()["consecutive_failures"] - 1)))
        self.log.schedule_retry(0.0 if outcome in TRANSIENT_OUTCOMES else delay)
        self._maybe_alert(outcome, detail, reason)
        return result

    def _outcome(self, outcome, ok, detail):
        return {"outcome": outcome, "ok": bool(ok), "detail": detail}

    def _maybe_alert(self, outcome, detail, reason):
        """Tell the operator once per problem, not once per attempt.

        A changed outcome is new information and gets through immediately; the
        same failure repeating is throttled for ``alert_cooldown`` seconds, so a
        dead sidecar cannot turn into a mail bomb on a fifteen-minute loop. A
        server that was simply never configured, or a second concurrent request,
        is not an outage and is not mailed about.
        """
        if self.alert is None or outcome in _NEVER_ALERT:
            return
        alerted_outcome, alerted_at = self.log.alert_state()
        now = self.clock()
        if alerted_outcome == outcome and now - alerted_at < self.alert_cooldown:
            return
        self.log.mark_alerted(outcome)
        try:
            self.alert(outcome, detail, reason, self.log.snapshot())
        except Exception:
            # The alert is the report, never the job: a broken SMTP must not make
            # the sign-in look like it failed for a different reason.
            logger.exception("Could not send the YouTube sign-in failure alert")

    def status(self):
        state = self.log.snapshot()
        state.update(self.account.describe())
        state["enabled"] = self.enabled
        state["needs_human"] = state.get("last_outcome") in HUMAN_OUTCOMES
        return state


_COPY = {
    "signed_in": "Signed in and saved the download cookies.",
    "need_identifier": "Google kept asking for the account address, so it does not accept the one configured.",
    "need_password": "Google asked for a password and did not accept the one configured.",
    "need_totp": "Google asked for an authenticator code and the answer was not accepted.",
    "need_backup_code": "Google asked for a backup code and the answer was not accepted.",
    "bad_code": "Google rejected the code that was typed.",
    "need_choice": "Google offered a choice of 2-step methods and none could be answered automatically.",
    "need_prompt": "Google asked for approval on a phone, which only you can tap.",
    "need_phone": "Google sent a code to a phone number this server cannot read.",
    "need_passkey": "Google asked for a passkey, which this server does not have.",
    "captcha": "Google asked for a captcha, so it will not accept an automated sign-in.",
    "blocked": "Google refused this browser. Sign in by hand from the desktop view instead.",
    "locked": "Google has locked the account after failed attempts. Wait for the lock to lift.",
    "unknown_account": "Google does not recognise the configured account.",
    "wrong_password": "Google says the configured password is wrong.",
    "consent": "Google showed a consent page before the sign-in.",
    "unsupported": "Google showed a page this server does not know how to answer.",
    "timeout": "Google did not finish answering in time.",
    _NO_ANSWER: "The configured 2-step credential has no answers left to give.",
    _SIDECAR_FAILED: "The browser service did not answer.",
    _NOT_CONFIGURED: "No Google account is configured for this server.",
    _BUSY: "Another sign-in is already running.",
}


def describe_outcome(outcome):
    """The operator-facing sentence for one outcome; never a credential."""
    return _COPY.get(outcome, f"Sign-in stopped at an unexpected state ({outcome}).")
