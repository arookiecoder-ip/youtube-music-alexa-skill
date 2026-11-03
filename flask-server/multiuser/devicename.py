"""A name a person recognises, derived from what a browser chose to declare.

Session lists used to print the raw ``User-Agent`` header as the device's label,
which meant the row for somebody's phone read
``Mozilla/5.0 (Linux; Android 14; SM-A546B) AppleWebKit/537.36 (KHTML, like``
-- truncated mid-token, because the header is longer than the column. That string
is evidence, not a name: it cannot be scanned, and it cannot be compared against
the list of devices you actually own when the point of reading it is to notice one
you do not recognise.

So the label is the browser and the platform, and nothing else. The header itself
stays available as a secondary detail -- the full text is what an administrator
auditing an intrusion needs, and it is what makes a claim like "Chrome 118 on
Windows" trustworthy when it turns out to be a scripted client wearing that string.

The rules below are the well-known shape of the header, not an exhaustive
classifier. Anything unrecognised answers ``""`` so the caller decides what an
unknown device should be called, rather than this module inventing a confident
wrong answer.
"""
from __future__ import annotations

DEVICE_LABEL_MAX_LEN = 48

# Checked in order, and the order carries the reasoning: a Chromium edge on
# Windows declares "Chrome" as well as "Edg", an Android Chrome tab declares
# "Linux", and an iPad pretending to be a desktop declares "Macintosh" with a
# touch-capable platform behind it.
_BROWSERS: tuple[tuple[str, str], ...] = (
    ("edg/", "Edge"),
    ("opr/", "Opera"),
    ("edga/", "Edge"),          # Edge on Android, which also says Chrome
    ("oppad/", "Opera"),
    ("firefox/", "Firefox"),
    ("fennec/", "Firefox"),
    ("crios/", "Chrome"),       # Chrome on iOS
    ("chrome/", "Chrome"),
    ("chromeframe/", "Chrome"),
    ("safari/", "Safari"),
    ("curl/", "curl"),
    ("wget/", "wget"),
    ("python-requests/", "python-requests"),
    ("okhttp/", "Android app"),
    ("googlebot/", "Googlebot"),
    ("bingbot/", "Bingbot"),
)

# Also ordered: Android before Linux, since an Android header contains the word
# "Linux"; "U; Linux" and "X11" are desktop Linux and must not read as Android.
_PLATFORMS: tuple[tuple[str, str], ...] = (
    ("iphone", "iPhone"),
    ("ipad", "iPad"),
    ("ipod", "iPod touch"),
    ("android", "Android"),
    ("cros", "ChromeOS"),
    ("mac os x", "macOS"),
    ("macintosh", "macOS"),
    ("windows nt", "Windows"),
    ("windows phone", "Windows Phone"),
    ("x11", "Linux"),
    ("linux", "Linux"),
    ("kaios", "KaiOS"),
    ("playstation", "PlayStation"),
)


def _browser(token: str, ua: str) -> str:
    """Chrome is the same product on every platform; Safari is not the same one.

    "Safari" in an Android or Windows header is not Apple's browser -- it is the
    compatibility token a webview or a spoofing client carries -- so calling that
    row "Safari on Android" would be actively misleading. Chromium itself is
    Chromium everywhere, so it keeps its name.
    """
    if token == "Safari" and not _is_apple(ua):
        return "Browser"
    return token


def _is_apple(ua: str) -> bool:
    return any(hay in ua for hay in ("iphone", "ipad", "ipod", "mac os x", "macintosh"))


def describe_user_agent(user_agent: str | None) -> str:
    """``"Chrome on Windows"``, or ``""`` when nothing here is recognisable.

    Never raises and never claims more than it read: an empty header, a header
    that names a browser but no platform, and a header that is only the
    ``Mozilla/5.0`` preamble each get a different answer rather than a guess.
    """
    ua = (user_agent or "").strip()
    if not ua:
        return ""
    lowered = ua.lower()

    browser = ""
    for needle, name in _BROWSERS:
        if needle in lowered:
            browser = _browser(name, lowered)
            break

    platform = ""
    for needle, name in _PLATFORMS:
        if needle in lowered:
            platform = name
            break

    if browser and platform:
        return f"{browser} on {platform}"[:DEVICE_LABEL_MAX_LEN]
    if browser:
        return browser[:DEVICE_LABEL_MAX_LEN]
    # An unrecognisable string is still worth showing when it is short and
    # self-describing ("spotify-ipc/1.2"); anything longer is noise a person
    # cannot read, so the caller is asked to fall back to its own wording.
    if len(ua) <= DEVICE_LABEL_MAX_LEN and "mozilla/5.0" not in lowered:
        return ua
    return ""
