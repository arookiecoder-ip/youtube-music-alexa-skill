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
import base64
import logging
import re
import time
from urllib.parse import urlparse

logger = logging.getLogger("google_signin")

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
METHOD_UNAVAILABLE = "method_unavailable"

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

# The three ways this popup offers to verify.  They are the only choices the
# browser can carry out; Google's chooser may also list a passkey or a recovery
# email, and neither is something a typed answer can finish.
METHODS = ("totp", "phone", "backup")
_METHOD_PATTERNS = {
    "totp": ("authenticator", "google authenticator"),
    # Only the row that actually sends a text. "Get a verification code" alone
    # also matches the authenticator row, which is how a phone choice once landed
    # on a TOTP prompt the account did not have.
    "phone": ("verification code at", "get a code at", "text message",
              "text a code", "text me a code", "send a code", "voice call"),
    "backup": ("backup code",),
}

_CAPTCHA_INPUTS = ("input[name=ca]", "input#ca", "input[type=text]",
                   "textarea[name=ca]")
_CONSENT_LABELS = ("I agree", "Accept all", "Alle akzeptieren", "Accept")
# A US or international number as Google prints it in "we sent a code to ...".
_PHONE_RE = re.compile(r"\+?\d[\d\s().-]{6,}\d")
# Google's own mask for a number it will not print in full ("a code at •••50",
# or "•••-•••-1234"): whatever digits follow the mask are the hint.
_MASKED_PHONE_RE = re.compile(
    r"[\u2022*\u00b7][\s\u2022*\u00b7().-]{0,20}?(\d{2,6})\b")
# The two-digit number a device prompt asks the person to match on the phone.
_MATCH_RE = re.compile(r"(?:tap|number)\D{0,40}(\d{2})(?!\d)", re.IGNORECASE)
# Headings that only the method list uses. They outrank nearby refusal copy.
_CHOOSER_HEADINGS = ("choose how you want to sign in", "choose how to verify",
                     "choose a 2-step verification method",
                     "choose a way to verify", "select a way to verify")
# Google's copy when the text itself could not be sent to the number on file.
_PHONE_SEND_FAILURES = ("couldn't verify your phone number",
                        "could not verify your phone number",
                        "couldn't send a code", "could not send a code",
                        "can't send a code", "cannot send a code")


def _matches(text, patterns):
    # Google's copy uses typographic apostrophes ("Couldn’t find this account"),
    # so fold them before comparing with the plain-spelled patterns.
    lowered = (text or "").lower().replace("\u2019", "'")
    return any(pattern in lowered for pattern in patterns)


def phone_hint(text):
    """The phone a code went to, masked down to its last four digits.

    Google writes the number inline ("Enter the code we sent to +1 555 010
    1234").  The person only needs enough to recognise which phone rang, so the
    rest is kept out of the page and out of logs.  When Google itself masks the
    number ("a code at •••••• •••50"), the digits it shows are the hint.
    """
    best = ""
    for match in _PHONE_RE.finditer(text or ""):
        digits = re.sub(r"\D", "", match.group(0))
        if 8 <= len(digits) <= 15 and len(digits) > len(re.sub(r"\D", "", best)):
            best = match.group(0)
    digits = re.sub(r"\D", "", best)
    if len(digits) >= 8:
        return "•••• " + digits[-4:]
    masked = _MASKED_PHONE_RE.search(text or "")
    if masked:
        return "•••• " + masked.group(1)[-4:]
    return ""


def prompt_hint(text):
    """The two-digit number a "tap Yes" prompt asks the person to match."""
    match = _MATCH_RE.search(text or "")
    return match.group(1) if match else ""


def device_hint(text):
    """The devices Google says it notified, so the person knows where to look."""
    match = re.search(r"sent a notification to\s+(.+?)(?:\.|$)", text or "",
                      re.IGNORECASE | re.DOTALL)
    return re.sub(r"\s+", " ", match.group(1)).strip()[:120] if match else ""


def captcha_hint(text):
    """Google's own instruction for the picture grid, if it printed one."""
    match = re.search(r"(?:select|choose|click)\s+(?:all\s+)?[^.!?\n]{0,120}",
                      text or "", re.IGNORECASE)
    return re.sub(r"\s+", " ", match.group(0)).strip()[:160] if match else ""


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
    if host == "consent.youtube.com" or host.endswith(".consent.youtube.com") or (
            host in ("accounts.google.com", "www.youtube.com", "youtube.com")
            and "/client/cookie/consent" in path):
        return CONSENT

    if "/challenge/totp" in path or code_kind == "totp":
        return NEED_TOTP
    if "/challenge/backup" in path or code_kind == "backup":
        return NEED_BACKUP_CODE
    if code_kind == "generic":
        return NEED_BACKUP_CODE if _matches(text, ["backup code"]) else NEED_TOTP

    # An explicit chooser heading outranks the refusal prose below. When a text
    # message cannot be sent, Google paints "We couldn't verify your phone number
    # ... too many failed attempts" *above* the same method list, and reading that
    # banner as an account lock hides the ways out that the page is still offering.
    if _matches(text, _CHOOSER_HEADINGS):
        return NEED_CHOICE

    # Refusals first: a page that says the password was wrong also has a password
    # box on it, and "wrong password" is the more useful answer.
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

    # Fields before prose. Google's password and identifier pages both carry a
    # "Try another way" link, and reading that link as a method chooser (as this
    # used to, whenever it painted before the field) skipped the password
    # entirely and offered verification methods for a session that was not even
    # signed in. A visible field is unambiguous, so it decides first.
    if fields.get("password") or "/challenge/pwd" in path:
        return NEED_PASSWORD
    if fields.get("identifier"):
        return NEED_IDENTIFIER

    if "/challenge/dp" in path:
        # Google's "device prompt" step. The same URL also hosts the chooser that
        # "Try another way" opens, and that chooser's first row reads "Tap Yes on
        # your phone or tablet" -- so the chooser heading has to be recognised
        # before the row copy, or the list is mistaken for the prompt again.
        if _matches(text, _CHOOSER_HEADINGS):
            return NEED_CHOICE
        if _matches(text, ["tap yes", "tap the notification", "check your device",
                           "tap the number", "match the number",
                           "on your phone", "on your tablet"]):
            return NEED_PROMPT
        if _matches(text, ["choose", "try another way", "another way",
                           "verification method"]):
            return NEED_CHOICE
        return UNSUPPORTED

    if "/challenge/selection" in path or "/challenge/skip" in path:
        # The list of ways to verify. It names every method on offer -- a passkey
        # row among them -- so the page is a choice even though the word appears.
        return NEED_CHOICE

    if "/challenge/prompt" in path or _matches(
            text, ["tap send now", "approval prompt"]):
        return NEED_PROMPT
    if ("/challenge/ipp" in path or "/challenge/pc" in path
            or _matches(text, ["enter the code we texted", "phone number on file",
                               "we sent a code"])):
        return NEED_PHONE
    if ("/challenge/emb" in path or "/challenge/az" in path
            or _matches(text, ["passkey", "unlock your phone"])):
        return NEED_PASSKEY
    if _matches(text, ["choose a 2-step verification method", "choose how to verify",
                       "try another way", "another way", "verification method"]):
        return NEED_CHOICE

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
    previous_url = urlparse(previous.get("url") or "").path
    deadline = time.time() + wait_seconds
    last = {}
    unsupported_since = None
    signature = None
    while time.time() < deadline:
        last = read_page_state(page)
        outcome = classify_page(last)
        text_now = (last.get("text") or "").strip()
        signature_now = (outcome, bool(text_now), _still_loading(text_now),
                         "tap yes" in text_now.lower())
        if signature_now != signature:
            signature = signature_now
            logger.info("Wait saw %s (%d chars, loading=%s, tap_yes=%s) at %s",
                        outcome, len(text_now), signature_now[2], signature_now[3],
                        urlparse(last.get("url") or "").path)
        if outcome == UNSUPPORTED:
            text = (last.get("text") or "").strip()
            if not text or _still_loading(text):
                # Blank while Google navigates, or its loading shell: neither an
                # answer nor grounds for one.
                unsupported_since = None
            elif unsupported_since is None:
                unsupported_since = time.time()
            # A readable page we cannot name, which stopped changing: classify
            # the settled copy once more, because the first read can catch the
            # shell and the real question arrives a breath later.
            elif time.time() - unsupported_since >= 1.5:
                again = _settled(page, last)
                if again is not None:
                    fresh = classify_page(again)
                    if fresh != UNSUPPORTED:
                        return again, fresh
                    if not _still_loading(again.get("text")):
                        return again, UNSUPPORTED
                    # Still the loading shell: the real question is behind it.
                    unsupported_since = None
        elif outcome == previous_question:
            # Still asking the same thing; the click has not been answered yet.
            unsupported_since = None
        else:
            return last, outcome
        time.sleep(0.3)
    # The clock ran out.  Read the final page once more before deciding: a slow
    # hydration can land the real question just as the deadline passes, and
    # calling that a timeout would throw away the only answer worth having.
    # The loop's last snapshot is already a few hundred milliseconds old, and the
    # challenge card can paint in exactly that window -- which is how a device
    # prompt was read as an unknown wall twice in a row.
    final_state = last
    for attempt in range(12):
        try:
            fresh_state = read_page_state(page)
        except Exception:
            break
        fresh_outcome = classify_page(fresh_state)
        logger.info("Deadline probe %d: %s at %s (%d chars, loading=%s)",
                    attempt + 1, fresh_outcome,
                    urlparse(fresh_state.get("url") or "").path,
                    len((fresh_state.get("text") or "").strip()),
                    _still_loading(fresh_state.get("text") or ""))
        if fresh_outcome not in (UNSUPPORTED, TIMEOUT):
            return fresh_state, fresh_outcome
        final_state = fresh_state
        text = (fresh_state.get("text") or "").strip()
        if text and not _still_loading(text):
            break
        time.sleep(0.8)
    final = classify_page(final_state)
    if final != previous_question and final != UNSUPPORTED:
        return final_state, final
    logger.info("Wait ended as unsupported at %s (fields=%s, %d chars, loading=%s)",
                (urlparse(final_state.get("url") or "").path),
                sorted((final_state.get("fields") or {}).keys()),
                len((final_state.get("text") or "").strip()),
                _still_loading(final_state.get("text") or ""))
    moved = urlparse(final_state.get("url") or "").path != previous_url
    # The URL moved on but never painted a page we can name.  That is a wall we
    # do not understand, not a slow network, and the caller must not throw away
    # the excerpt that says so.
    return final_state, UNSUPPORTED if moved else TIMEOUT


_LOADING_COPY = ("loading 2-step verification", "checking your browser",
                 "just a moment", "one moment", "loading…", "loading...")


def _still_loading(text):
    """True while Google's shell is on screen and the real question has not painted."""
    return _matches(text, _LOADING_COPY)


def _settled(page, snapshot):
    """The page a moment later, or None while it is still moving.

    Prompt pages carry a countdown, so a whole-snapshot comparison never agrees.
    The URL and the fields are what decide which question is being asked; the
    text only has to be there, and not be Google's loading shell, for the caller
    to read and classify it again.
    """
    time.sleep(0.4)
    try:
        again = read_page_state(page)
    except Exception:
        return None
    if (again.get("url") == snapshot.get("url")
            and again.get("fields") == snapshot.get("fields")
            and (again.get("text") or "").strip()):
        return again
    return None


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


def _credential_origin(page):
    parsed = urlparse(page.url)
    if parsed.scheme != 'https' or parsed.hostname != 'accounts.google.com' or parsed.port not in (None, 443):
        raise ValueError('Refusing credentials on an unexpected origin')


def submit_identifier(page, email, wait_seconds=20):
    state = _wait_for_input(page, "identifier")
    selector = state["fields"].get("identifier")
    if not selector:
        return state, classify_page(state)
    _credential_origin(page)
    _type(page, selector, email)
    _credential_origin(page)
    _submit(page)
    return _wait_after(page, state, wait_seconds)


def submit_password(page, password, wait_seconds=60):
    # The device-prompt challenge paints a "Loading 2-Step Verification" shell
    # while Google dispatches the notification; on a cold profile that shell can
    # sit there for most of a minute before "Tap Yes" arrives. Giving up at 20s
    # read the shell as an unknown wall instead of the approval it was.
    state = _wait_for_input(page, "password")
    selector = state["fields"].get("password")
    if not selector:
        return state, classify_page(state)
    _credential_origin(page)
    _type(page, selector, password)
    _credential_origin(page)
    _submit(page)
    return _wait_after(page, state, wait_seconds)


def submit_code(page, code, wait_seconds=25):
    """Answer whichever code challenge is on screen (authenticator or backup)."""
    state = _wait_for_input(page, "code")
    selector = state["fields"].get("code")
    if not selector:
        return state, classify_page(state)
    _credential_origin(page)
    _type(page, selector, code)
    _credential_origin(page)
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


def click_consent(page):
    """Click through the EU cookie wall, if one is on screen."""
    for label in _CONSENT_LABELS:
        try:
            button = page.get_by_text(label, exact=True).first
            if button.is_visible():
                button.click(timeout=3000)
                return True
        except Exception:
            continue
    for label in _CONSENT_LABELS:
        try:
            button = page.get_by_text(label, exact=False).first
            if button.is_visible():
                button.click(timeout=3000)
                return True
        except Exception:
            continue
    return False


def accept_consent(page, wait_seconds=10):
    """Click through the EU cookie wall, which is not a login failure."""
    state = read_page_state(page)
    if not click_consent(page):
        return state, CONSENT
    return _wait_after(page, state, wait_seconds)


def _click_label(page, patterns):
    """Click the first visible control whose text contains one of ``patterns``.

    Matching body text with ``get_by_text`` also matches every ancestor card that
    contains the words, and clicking an ancestor lands on empty space in the
    middle of the card -- that is what made "Try another way" inert on the device
    prompt. Buttons and links are the controls; the innermost text node is the
    fallback for controls that are plain clickable text.
    """
    for pattern in patterns:
        expression = re.compile(re.escape(pattern), re.IGNORECASE)
        for role in ("button", "link"):
            try:
                found = page.get_by_role(role, name=expression)
                if found.count() and found.first.is_visible():
                    if _press(page, found.first):
                        return True
            except Exception:
                continue
        try:
            found = page.get_by_text(expression)
            count = found.count()
        except Exception:
            continue
        # Ancestors come first in DOM order; the last visible match is the one
        # whose click does not disappear into a card's padding.
        for index in range(count - 1, -1, -1):
            try:
                item = found.nth(index)
                if item.is_visible() and _press(page, item):
                    return True
            except Exception:
                continue
    return False


def _press(page, target):
    """Click a control, falling back to its own handler when something overlays it.

    A consent footer or a sticky prompt bar can cover a row that is genuinely
    visible; Playwright refuses that click, and treating the refusal as "the row
    is not here" moved the ladder to the wrong method. The element's own click
    handler is the row's real action, so it is a safe last resort.
    """
    try:
        target.click(timeout=5000)
        return True
    except Exception:
        try:
            target.evaluate("el => el.click()")
            return True
        except Exception:
            return False


def show_methods(page, wait_seconds=20):
    """Reach the full chooser and leave it on screen for the person to pick.

    Google's first challenge page often lists only the phone prompt; the three
    methods this popup can answer live one door deeper behind "Try another way".
    """
    state = read_page_state(page)
    outcome = classify_page(state)
    if outcome == NEED_CHOICE:
        return state, outcome
    if _click_label(page, ("try another way", "another way", "more ways to verify")):
        return _wait_after(page, state, wait_seconds)
    return state, outcome


def choose_method(page, method, wait_seconds=25):
    """Pick one of the three methods, from whichever challenge page is showing."""
    labels = _METHOD_PATTERNS.get(method)
    if not labels:
        return read_page_state(page), UNSUPPORTED
    state = read_page_state(page)
    outcome = classify_page(state)
    if outcome != NEED_CHOICE:
        # The prompt / phone page carries the same door; opening it first is what
        # makes the chosen method clickable rather than invisible.
        if _click_label(page, ("try another way", "another way", "more ways to verify")):
            state = read_page_state(page)
    # "Get a verification code at +1 ..." is the phone row; "Authenticator app"
    # and "Enter one of your backup codes" name themselves.
    if not _click_label(page, labels):
        # The chooser's own row copy is what tells us a pattern is missing; the
        # account address on the page is not part of it.
        seen = []
        try:
            for role in ("button", "link"):
                for element in page.get_by_role(role).all()[:25]:
                    try:
                        if element.is_visible():
                            label = (element.inner_text() or "").strip()
                            if label:
                                seen.append(label[:60])
                    except Exception:
                        continue
        except Exception:
            pass
        logger.info("choose_method(%s) found no matching row; controls=%r", method, seen)
        return read_page_state(page), METHOD_UNAVAILABLE
    state, outcome = _wait_after(page, state, wait_seconds)
    if method == "phone" and outcome in (NEED_TOTP, NEED_BACKUP_CODE):
        # A text row can never lead to a code app or a backup list. Landing there
        # means the click found the wrong row; say so instead of demanding a code
        # the person did not choose.
        logger.info("choose_method(phone) landed on %s", outcome)
        return state, METHOD_UNAVAILABLE
    if method == "phone" and _matches(state.get("text") or "",
                                      _PHONE_SEND_FAILURES):
        # Google re-opened the chooser above "We couldn't verify your phone
        # number". Saying the text failed is honest; quietly showing the list
        # again makes the person pick the same row a second time.
        logger.info("choose_method(phone) was refused: %s",
                    (state.get("text") or "")[:120].replace("\n", " "))
        return state, METHOD_UNAVAILABLE
    return state, outcome


def captcha_snapshot(page):
    """What the security check looks like, plus how it expects to be answered."""
    state = read_page_state(page)
    shot = page.screenshot(type="jpeg", quality=70)
    try:
        width, height = page.evaluate("() => [window.innerWidth, window.innerHeight]")
    except Exception:
        width, height = 0, 0
    return state, {
        "image": base64.b64encode(shot).decode("ascii"),
        "text": bool(_present(page, _CAPTCHA_INPUTS)),
        "hint": captcha_hint(state.get("text") or ""),
        "viewport": [int(width), int(height)],
    }


def answer_captcha(page, text="", points=None, wait_seconds=25):
    """Answer a captcha by typing or by tapping the pictures Google named."""
    state = read_page_state(page)
    text = (text or "").strip()
    if text:
        selector = _present(page, _CAPTCHA_INPUTS)
        if not selector:
            return state, "captcha_needs_taps"
        _type(page, selector, text)
        _submit(page)
    else:
        try:
            width, height = page.evaluate("() => [window.innerWidth, window.innerHeight]")
        except Exception:
            width, height = 0, 0
        if not width or not height or not points:
            return state, CAPTCHA
        for point in points[:20]:
            try:
                x = min(max(float(point[0]), 0.0), 1.0)
                y = min(max(float(point[1]), 0.0), 1.0)
                page.mouse.click(x * width, y * height)
                time.sleep(0.25)
            except (TypeError, ValueError, IndexError):
                continue
        if not _click_label(page, ("verify", "next", "submit", "continue")):
            page.keyboard.press("Enter")
    return _wait_after(page, state, wait_seconds)
