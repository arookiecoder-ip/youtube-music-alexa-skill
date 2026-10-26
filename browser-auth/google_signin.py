"""Drive Google's sign-in form far enough to hand a session back to the app.

Two halves live here on purpose.  ``classify_page`` is pure: it turns a snapshot
of what the browser is showing into one outcome string, and every branch of the
login ladder is therefore testable without a browser or an account.  The driver
below is the thin Playwright layer that produces those snapshots.

The credentials arrive from the Flask app over the loopback control API on every
call and are never stored, echoed or logged here: this container holds no secret
of its own, so a wiped profile volume cannot lose one and a leaked image cannot
ship one.

Every outcome that is not ``signed_in`` is still a useful answer.  A human owns
the remaining paths -- the noVNC desktop for a captcha, their phone for a prompt
-- so the caller needs to know *which* wall it hit, not merely that one exists.
"""
import re
import time
from urllib.parse import urlparse

# Outcomes. Anything outside this set is a bug, not a state.
SIGNED_IN = "signed_in"
NEED_IDENTIFIER = "need_identifier"
NEED_PASSWORD = "need_password"
NEED_TOTP = "need_totp"
NEED_BACKUP_CODE = "need_backup_code"
NEED_CHOICE = "need_choice"
NEED_PROMPT = "need_prompt"
NEED_PHONE = "need_phone"
NEED_PASSKEY = "need_passkey"
WRONG_PASSWORD = "wrong_password"
BAD_CODE = "bad_code"
UNKNOWN_ACCOUNT = "unknown_account"
CAPTCHA = "captcha"
LOCKED = "locked"
BLOCKED = "blocked"
CONSENT = "consent"
TIMEOUT = "timeout"
UNSUPPORTED = "unsupported"

# The two states a machine can answer without a human.
ANSWERABLE = (NEED_TOTP, NEED_BACKUP_CODE)
# States where a human with the noVNC desktop can finish the job.
NEEDS_HUMAN = (NEED_CHOICE, NEED_PROMPT, NEED_PHONE, NEED_PASSKEY, CAPTCHA,
               BLOCKED, CONSENT, WRONG_PASSWORD, UNKNOWN_ACCOUNT, LOCKED)

SIGNIN_URL = ("https://accounts.google.com/signin/v2/identifier"
              "?flowName=GlifWebSignIn&flowEntry=ServiceLogin"
              "&continue=https%3A%2F%2Fmusic.youtube.com%2F")

_YOUTUBE_HOSTS = ("music.youtube.com", "www.youtube.com", "youtube.com")
# Where Google sends an authenticated browser for account housekeeping.
_POST_AUTH_HOSTS = ("gds.google.com", "myaccount.google.com")

_IDENTIFIER_SELECTORS = (
    "input#identifierId",
    "input#identifier",
    "input[type=email][name=identifier]",
    "input[name=identifier]",
    "input[autocomplete^=username]",
)
_PASSWORD_SELECTORS = (
    "input#password",
    "input[type=password]",
    "input[autocomplete=current-password]",
)
# Tagged so the classifier can tell an authenticator code from a backup code
# before it has to guess from the wording on screen.
_CODE_SELECTORS = (
    (("input#totpPin", "input[name=totpPin]", "input#idvPreregisteredTurn2Input"), "totp"),
    (("input#backupCodePin", "input[name=backupCodePin]"), "backup"),
    (("input[aria-label*='code' i]", "input[inputmode=numeric]"), "generic"),
)
# Google's current form has no ids on its buttons: the visible "Next" is a
# type=button, while the only type=submit on the page is a hidden "Scroll down"
# helper. Matching the label before the generic submit is what keeps the
# identifier from being typed into a page that never sends it.
_NEXT_SELECTORS = ("#identifierNext", "#passwordNext", "#totpPin-next",
                   "button:has-text('Next')", "button:has-text('Verify')",
                   "div[jsname='V67aGc']", "button[type=submit]")

# Google's 2-step chooser lists several methods; only these lead somewhere a
# server holding a TOTP secret or a backup code can follow.
_CHOICE_PATTERNS = ("authenticator app", "enter a code", "backup code",
                    "try another way", "another way")


def _matches(text, patterns):
    # Google's copy uses typographic apostrophes ("Couldn’t find this account"),
    # so fold them before comparing with the plain-spelled patterns.
    lowered = (text or "").lower().replace("\u2019", "'")
    return any(pattern in lowered for pattern in patterns)


def classify_page(snapshot):
    """One login state from what the browser can see.

    ``snapshot`` keys: ``url``, ``text`` (visible body text) and ``fields`` -- the
    input selectors actually on the page, as ``{family: selector}`` plus
    ``fields['code_kind']``.  URL and field identity first, because Google's own
    paths are unambiguous; body copy next, because the error wording is stable
    even when the path is not.
    """
    snapshot = snapshot or {}
    url = snapshot.get("url") or ""
    text = snapshot.get("text") or ""
    fields = snapshot.get("fields") or {}
    try:
        parsed = urlparse(url)
    except (TypeError, ValueError):
        return UNSUPPORTED
    path = (parsed.path or "").lower()
    host = (parsed.hostname or "").lower()
    code_kind = fields.get("code_kind") or ""

    if host in _YOUTUBE_HOSTS and "/signin" not in path and "/serviceauth" not in path:
        # Google bounced us back to YouTube Music. Whether the session is really
        # signed in is settled afterwards by the account-menu probe, not here.
        return SIGNED_IN
    if (host in _POST_AUTH_HOSTS or host.endswith(".myaccount.google.com")) \
            and "/signin" not in path:
        # Account prompts (add recovery info, a selfie) live outside the sign-in
        # flow and only appear once the password and any second factor were
        # accepted. They do not gate the session; the capture step's account-menu
        # probe is the real verdict.
        return SIGNED_IN
    if host == "accounts.google.com" and path.startswith("/sorry/"):
        return CAPTCHA
    if host.endswith("consent.youtube.com") or "/client/cookie/consent" in path:
        return CONSENT

    if "/challenge/totp" in path or code_kind == "totp":
        return NEED_TOTP
    if "/challenge/backup" in path or code_kind == "backup":
        return NEED_BACKUP_CODE
    if code_kind == "generic":
        return NEED_BACKUP_CODE if _matches(text, ["backup code"]) else NEED_TOTP
    if "/challenge/skip" in path or _matches(
            text, ["choose a 2-step verification method", "try another way", "another way"]):
        return NEED_CHOICE
    if "/challenge/prompt" in path or _matches(
            text, ["tap send now", "on your phone", "approval prompt"]):
        return NEED_PROMPT
    if ("/challenge/ipp" in path or "/challenge/pc" in path
            or _matches(text, ["enter the code we texted", "phone number on file",
                               "we sent a code"])):
        return NEED_PHONE
    if ("/challenge/emb" in path or "/challenge/az" in path
            or _matches(text, ["passkey", "unlock your phone"])):
        return NEED_PASSKEY

    if _matches(text, ["no account found", "couldn't find your account",
                       "couldn't find this account", "couldn't find your google account",
                       "not a google account"]):
        return UNKNOWN_ACCOUNT
    if _matches(text, ["too many failed attempts", "temporarily locked",
                       "your account is locked", "account has been temporarily"]):
        return LOCKED
    if _matches(text, ["captcha", "unusual traffic", "confirm you're not a robot"]):
        return CAPTCHA
    if _matches(text, ["may not be secure", "couldn't sign you in",
                       "use a different browser"]):
        return BLOCKED
    if _matches(text, ["wrong password", "you entered the wrong password",
                       "invalid password"]):
        return WRONG_PASSWORD
    if _matches(text, ["wrong code", "couldn't verify", "invalid code",
                       "doesn't match", "that code didn't work"]):
        return BAD_CODE

    if fields.get("password"):
        return NEED_PASSWORD
    if "/challenge/pwd" in path:
        # The URL names the password step before the field has painted. Error
        # copy above still wins: a refused password keeps this same path.
        return NEED_PASSWORD
    if fields.get("identifier"):
        return NEED_IDENTIFIER
    if host == "accounts.google.com" and "/challenge" in path:
        # A challenge shape we do not recognise. Guessing is worse than stopping:
        # a wrong click here can spend a backup code or trip a lockout.
        return UNSUPPORTED
    return UNSUPPORTED


def _present(page, selectors, timeout=250):
    for selector in selectors:
        try:
            if page.locator(selector).first.is_visible():
                return selector
        except Exception:
            continue
    return ""


def _page_text(page):
    """Visible body text, tolerating the instant when a navigation replaces it."""
    for reader in (
        lambda: page.inner_text("body", timeout=2000),
        lambda: page.evaluate("() => document.body ? document.body.innerText : ''"),
    ):
        try:
            text = reader() or ""
        except Exception:
            continue
        if text.strip():
            return text
    return ""


def read_page_state(page):
    """A snapshot of the login page: where it is, what it says, what it asks for."""
    text = _page_text(page)
    fields = {}
    for selectors, kind in _CODE_SELECTORS:
        found = _present(page, selectors)
        if found:
            fields["code"] = found
            fields["code_kind"] = kind
            break
    identifier = _present(page, _IDENTIFIER_SELECTORS)
    if identifier:
        fields["identifier"] = identifier
    password = _present(page, _PASSWORD_SELECTORS)
    if password:
        fields["password"] = password
    return {"url": page.url, "text": text[:4000], "fields": fields}


def _type(page, selector, value, delay=45):
    target = page.locator(selector).first
    target.click()
    try:
        target.press_sequentially(value, delay=delay)
    except AttributeError:
        target.type(value, delay=delay)


def _submit_button(page):
    return _present(page, _NEXT_SELECTORS)


def _submit(page):
    selector = _submit_button(page)
    if selector:
        try:
            page.locator(selector).first.click(timeout=5000)
            return
        except Exception:
            pass
    page.keyboard.press("Enter")


def _wait_after(page, previous, wait_seconds):
    """Give Google time to answer, then classify whatever page arrived.

    The page before the submit is passed in because the identifier and password
    screens look the same until Google has processed a click: a classifier run a
    quarter of a second too early would report the same question and the ladder
    would type the address a second time. So the same question is not an answer,
    and an unreadable page is the navigation between two of them, not a refusal.
    """
    previous = previous or {}
    previous_question = classify_page(previous)
    deadline = time.time() + wait_seconds
    last = {}
    unsupported_since = None
    while time.time() < deadline:
        last = read_page_state(page)
        outcome = classify_page(last)
        if outcome == UNSUPPORTED:
            text = (last.get("text") or "").strip()
            if not text:
                # Blank while Google navigates or hydrates: neither an answer nor
                # grounds for one.
                unsupported_since = None
            elif unsupported_since is None:
                unsupported_since = time.time()
            # A readable page we cannot name, which stopped changing: report what
            # it says so the operator sees the real wall.
            elif time.time() - unsupported_since >= 1.5 and _stable(page, last):
                return last, outcome
        elif outcome == previous_question:
            # Still asking the same thing; the click has not been answered yet.
            unsupported_since = None
        else:
            return last, outcome
        time.sleep(0.3)
    return last, TIMEOUT


def _stable(page, snapshot):
    """True when a second read a moment later looks the same."""
    time.sleep(0.4)
    try:
        return read_page_state(page) == snapshot
    except Exception:
        return False


def open_signin(page, url=SIGNIN_URL, wait_seconds=12):
    """Land on the identifier screen, or report the state we actually reached."""
    page.goto(url, wait_until="domcontentloaded", timeout=45000)
    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        state = read_page_state(page)
        outcome = classify_page(state)
        if outcome not in (UNSUPPORTED, TIMEOUT):
            return state, outcome
        time.sleep(0.25)
    return read_page_state(page), TIMEOUT


def _wait_for_input(page, family, wait_seconds=8):
    """Wait out a navigation or re-render before deciding a field is absent.

    Google answers ``/signin/v2/identifier`` with a redirect to its current
    ``v3`` page, so a step that reads the screen once can catch the half-built
    shell. The field arrives a moment later and the step proceeds normally.
    """
    deadline = time.time() + wait_seconds
    state = read_page_state(page)
    while family not in (state.get("fields") or {}) and time.time() < deadline:
        time.sleep(0.3)
        state = read_page_state(page)
    return state


def submit_identifier(page, email, wait_seconds=15):
    state = _wait_for_input(page, "identifier")
    selector = state["fields"].get("identifier")
    if not selector:
        return state, classify_page(state)
    _type(page, selector, email)
    _submit(page)
    return _wait_after(page, state, wait_seconds)


def submit_password(page, password, wait_seconds=20):
    state = _wait_for_input(page, "password")
    selector = state["fields"].get("password")
    if not selector:
        return state, classify_page(state)
    _type(page, selector, password)
    _submit(page)
    return _wait_after(page, state, wait_seconds)


def submit_code(page, code, wait_seconds=25):
    """Answer whichever code challenge is on screen (authenticator or backup)."""
    state = _wait_for_input(page, "code")
    selector = state["fields"].get("code")
    if not selector:
        return state, classify_page(state)
    _type(page, selector, code)
    _submit(page)
    return _wait_after(page, state, wait_seconds)


def choose_two_step(page, wait_seconds=15):
    """On the challenge chooser, pick the authenticator or backup-code option.

    Returning ``need_choice`` again is a real answer: a chooser that lists only a
    phone prompt is not a page a server can finish.
    """
    state = read_page_state(page)
    lowered = (state.get("text") or "").lower()
    wanted = next((pattern for pattern in _CHOICE_PATTERNS if pattern in lowered), "")
    if not wanted:
        return state, classify_page(state)
    try:
        page.get_by_text(re.compile(re.escape(wanted), re.IGNORECASE)).first.click(timeout=5000)
    except Exception:
        return state, NEED_CHOICE
    return _wait_after(page, state, wait_seconds)


def accept_consent(page, wait_seconds=10):
    """Click through the EU cookie wall, which is not a login failure."""
    state = read_page_state(page)
    for label in ("I agree", "Accept all", "Alle akzeptieren"):
        try:
            page.get_by_text(label, exact=False).first.click(timeout=3000)
        except Exception:
            continue
        return _wait_after(page, state, wait_seconds)
    return state, CONSENT
