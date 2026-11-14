"""Pixel contract for /account, in real Chromium at the widths people use.

The page is a settings surface with four sections, a status strip, a jump nav, a
device list and the popups that own each action -- the kind of layout that breaks
quietly: a panel that no longer fits its column, a nav row that starts scrolling
the document sideways, a control that becomes a 19px tap target, a border radius
that came back. None of that is visible in a template diff, and all of it is
visible here.

Everything asserted is measurable, and the fixtures are deliberately the awkward
shape of each state: an unconfirmed address with a live code, a waiting two-factor
enrollment, two linked providers, five sessions with real headers, and long
values that a name column would otherwise clip.
"""
from pathlib import Path
import os
import threading

import pytest
from flask import Flask, Response, jsonify, render_template, request
from werkzeug.serving import make_server
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[2]
WIDTHS = [320, 375, 390, 768, 1024, 1440, 1920]
# What the page actually sent to the server, recorded by the stubs below. The
# layout checks are blind to a confirmation popup that opens and never sends, so
# the destructive actions are the one thing here worth counting in requests.
POSTS = []


def _qr_png():
    """A plain square, big enough to occupy the frame the real barcode would."""
    import struct
    import zlib

    def chunk(kind, data):
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))

    size = 148
    raw = b"".join(b"\x00" + b"\x00\xff" * (size * 3) for _ in range(size))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw))
            + chunk(b"IEND", b""))

CHROME = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) " \
         "Chrome/141.0.0.0 Safari/537.36"
IPHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 " \
         "(KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1"
PIXEL = "Mozilla/5.0 (Linux; Android 14; SM-A546B) AppleWebKit/537.36 (KHTML, like Gecko) " \
        "Chrome/126.0.6478.188 Mobile Safari/537.36"
FIREFOX = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:132.0) Gecko/20100101 Firefox/132.0"

FIXTURE = {
    "alexa_access_enabled": True,
    "username": "fairly_long_username_that_has_to_fit_somewhere",
    "email": "someone.with.a.longish.local.part@example-corporation.test",
    "email_verified": False,
    "verification_pending": True,
    "verification": {"pending": True, "email": "a@b.test", "expires_at": 1, "attempts_left": 2},
    "role": "player",
    "role_text": "Player",
    "is_admin": False,
    "created_by": "self",
    "created_at": 1730000000,
    "created_text": "Oct 26, 2024, 09:13",
    "must_change_password": True,
    "totp_enabled": False,
    "totp_pending": True,
    "providers": [
        {"provider": "amazon", "status": "connected", "last_error": "", "updated_at": 1,
         "last_validated_at": 1, "metadata": {}},
        {"provider": "youtube", "status": "expired", "last_error": "", "updated_at": 1,
         "last_validated_at": 1, "metadata": {}},
    ],
    "provider_map": {
        "amazon": {"provider": "amazon", "status": "connected"},
        "youtube": {"provider": "youtube", "status": "expired"},
    },
    "sessions": [
        {"id": "aaaa1111", "current": True, "created_at": 1730000000, "last_seen_at": 1730009000,
         "expires_at": 1730010000, "ip": "203.0.113.9", "device": "Chrome on Linux",
         "device_ua": CHROME, "seen_text": "Oct 26, 2024, 11:43", "signed_in_text": "Oct 26, 2024, 09:13"},
        {"id": "bbbb2222", "current": False, "created_at": 1729900000, "last_seen_at": 1729990000,
         "expires_at": 1730010000, "ip": "198.51.100.7", "device": "Safari on iPhone",
         "device_ua": IPHONE, "seen_text": "Oct 27, 2024, 08:06", "signed_in_text": "Oct 25, 2024"},
        {"id": "cccc3333", "current": False, "created_at": 1729800000, "last_seen_at": 1729890000,
         "expires_at": 1730010000, "ip": "198.51.100.44", "device": "Chrome on Android",
         "device_ua": PIXEL, "seen_text": "Oct 26, 2024", "signed_in_text": "Oct 24, 2024"},
        {"id": "dddd4444", "current": False, "created_at": 1729700000, "last_seen_at": 1729790000,
         "expires_at": 1730010000, "ip": "192.0.2.5", "device": "Firefox on Windows",
         "device_ua": FIREFOX, "seen_text": "Oct 25, 2024", "signed_in_text": "Oct 23, 2024"},
        {"id": "eeee5555", "current": False, "created_at": 1729600000, "last_seen_at": 1729700000,
         "expires_at": 1730010000, "ip": "192.0.2.6", "device": "",
         "device_ua": "", "seen_text": "Oct 22, 2024", "signed_in_text": "Oct 22, 2024"},
    ],
    "password_min_length": 12,
    "signup_open": True,
    # The code-proving state account.js reads; there is none live in this fixture.
    "reauth": None,
}


@pytest.fixture(scope="module")
def origin():
    app = Flask(__name__, template_folder=str(ROOT / "templates"),
                static_folder=str(ROOT / "templates/static"), static_url_path="/static")
    app.config["TEMPLATES_AUTO_RELOAD"] = True

    @app.route("/account")
    def account_page():
        # ?theme=light renders the same page in the other palette; the real route
        # reads the stored preference instead of a query string. ?step= is the
        # no-script fallback trigger: the server marks that popup data-autopen.
        wanted = request.args.get("theme")
        step = request.args.get("step", "")
        return render_template("account.html", app_name="Music Box", csrf_form="test-token",
                               alexa_invocation="music box multi",
                               asset_url=lambda relative: f"/static/{relative}?v=fixture",
                               theme=wanted if wanted in ("dark", "light") else "dark",
                               step=step if step in ("password", "reset", "email", "totp", "delete") else "",
                               **FIXTURE)

    @app.route("/api/account/code/check", methods=["POST"])
    def code_check():
        return jsonify({"ok": True})

    @app.route("/api/account")
    def api_account():
        # account.js reloads this after every action; the layout checks click
        # through a panel, so the endpoint has to answer like the real one.
        return jsonify(FIXTURE)

    @app.route("/api/profile_status/")
    def profile_status():
        return jsonify({"alexa_access_enabled": True, "amazon_connected": True, "youtube_own_session": True,
                        "youtube_server_fallback": False})

    @app.route("/api/account/totp/qr")
    def totp_qr():
        # The enrollment is waiting in this fixture, so account.js asks for the
        # barcode. A broken image would move the layout it is measured inside.
        return Response(_qr_png(), mimetype="image/png")

    @app.route("/api/account/providers/<provider>/disconnect", methods=["POST"])
    def provider_disconnect(provider):
        POSTS.append(f"disconnect:{provider}")
        return jsonify({"complete": True, "message": ""})

    # The provider sign-in popup submits to the same endpoints the standalone page
    # uses. These answer with the first follow-up each provider asks for, so the
    # dialog's step panels and its cancel-on-close path are exercised.
    @app.route("/api/youtube/browser-session/form/start", methods=["POST"])
    def youtube_form_start():
        POSTS.append("youtube:start")
        return jsonify({"state": "need_code", "answer": "securitycode",
                        "message": "Enter the code your phone shows."})

    @app.route("/api/youtube/browser-session/form/stop", methods=["POST"])
    def youtube_form_stop():
        POSTS.append("youtube:stop")
        return jsonify({"success": True})

    @app.route("/api/youtube/browser-session/form/code", methods=["POST"])
    def youtube_form_code():
        POSTS.append("youtube:code")
        return jsonify({"state": "connected"})

    @app.route("/alexa/form_login/", methods=["POST"])
    def amazon_form_start():
        POSTS.append("amazon:start")
        body = request.get_json(silent=True) or {}
        # The template fixture reports Amazon connected, so the popup tests use a
        # normal address for the approval path and this one to reach the choice
        # panel, which is the only place the popup grows radio rows.
        if body.get("email") == "choice@example.test":
            return jsonify({"state": "need_choice", "kind": "claimsoption",
                            "message": "Choose how to verify.",
                            "options": [
                                {"index": "0",
                                 "label": "Text a code to the number ending 1234"},
                                {"index": "1", "label": "Use an authenticator app"},
                            ]})
        return jsonify({"state": "need_approval",
                        "message": "Approve this sign-in on your phone."})

    @app.route("/alexa/form_login/step/", methods=["POST"])
    def amazon_form_step():
        body = request.get_json(silent=True) or {}
        POSTS.append("amazon:" + str(body.get("kind")))
        return jsonify({"state": "need_approval",
                        "message": "Approve this sign-in on your phone."})

    @app.route("/alexa/form_login/cancel/", methods=["POST"])
    def amazon_form_cancel():
        POSTS.append("amazon:cancel")
        return jsonify({"success": True})

    @app.route("/api/account/sessions/revoke", methods=["POST"])
    def sessions_revoke():
        body = request.get_json(silent=True) or {}
        POSTS.append("revoke-others" if body.get("others") else f"revoke:{body.get('id')}")
        return jsonify({})

    @app.route("/api/account/delete", methods=["POST"])
    def delete_account():
        POSTS.append("delete:check" if request.json.get("verify_only") else "delete:final")
        return jsonify({"ok": True})

    @app.route("/api/account/email/reauth", methods=["POST"])
    def delete_code():
        import time
        POSTS.append("reauth:send")
        return jsonify({"ok": True, "sent_to": "a@b.test", "expires_at": time.time() + 600, "resend_at": time.time() + 60})

    @app.errorhandler(404)
    def not_found(_error):
        # A POST the page has no stub for must not be a silent 500 in the middle
        # of a layout check; the shape of the response is the real one.
        return jsonify({"error": {"code": "not_stubbed", "message": "not stubbed"}}), 404

    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    thread.join()


@pytest.fixture
def page(origin):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        opened = context.new_page()
        opened.goto(f"{origin}/account", wait_until="networkidle")
        yield opened
        context.close()
        browser.close()


def at_width(page, origin, width):
    page.set_viewport_size({"width": width, "height": 900})
    page.reload(wait_until="networkidle")
    page.wait_for_timeout(120)


def open_section(page, panel_id):
    """Choose a section from the rail the way a person does, and wait for the panel
    to answer. Clicking the link rather than setting the hash is the point: the link
    is what the page offers, and the address is its consequence."""
    page.locator(f'.section-nav a[href="#{panel_id}"]').click()
    page.wait_for_selector(f"#{panel_id}:not([hidden])")
    page.wait_for_timeout(60)


DIALOGS = ["password", "email", "totp"]


def open_dialog(page, slug):
    """Open one popup from the button that owns it, the way a person does. The click
    is the point: the button is what the page offers, and opening in place is its
    promise -- no round trip to /account?step=... on this path."""
    page.locator(f'#panel-profile a[data-dialog="dialog-{slug}"]').first.click()
    page.wait_for_selector(f"#dialog-{slug}[open]")
    page.wait_for_timeout(80)


def hidden_panels(page):
    return page.evaluate("""() => [...document.querySelectorAll('.account-panes > .panel')]
        .filter((node) => node.hidden).map((node) => node.id)""")


def reveal_all(page):
    """Put every section back on the page -- the shape a browser with no JavaScript
    gets -- so a geometry sweep measures all four panels rather than whichever one
    the rail happens to be showing."""
    page.evaluate("""() => document.querySelectorAll('.account-panes > .panel')
        .forEach((panel) => { panel.hidden = false; })""")
    page.wait_for_timeout(60)


# --------------------------------------------------------------------- overflow
@pytest.mark.parametrize("width", WIDTHS)
def test_no_width_produces_horizontal_overflow(origin, width):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_context(viewport={"width": width, "height": 900}).new_page()
        page.goto(f"{origin}/account", wait_until="networkidle")
        page.set_viewport_size({"width": width, "height": 900})
        page.reload(wait_until="networkidle")
        # Every panel is measured, not just the one the rail chose to show: a section
        # that only overflows when it is a full-width column still overflows.
        reveal_all(page)
        report = page.evaluate("""() => {
          const doc = document.documentElement;
          const spillers = [];
          document.querySelectorAll('body *').forEach((node) => {
            if (node.hidden || !node.offsetParent) return;
            const box = node.getBoundingClientRect();
            // Only the right edge matters: an element parked off the left is this
            // page's own trick (the skip link, the honeypot), and the document
            // cannot scroll to it. Growing past the right edge is the defect.
            if (box.right > doc.clientWidth + 1) {
              spillers.push(node.tagName + '.' + (node.className || '') + ' @' + Math.round(box.right));
            }
          });
          return {scrollWidth: doc.scrollWidth, clientWidth: doc.clientWidth,
                  spillers: spillers.slice(0, 8)};
        }""")
        page.close()
        browser.close()
    assert report["scrollWidth"] <= report["clientWidth"] + 1, (width, report)
    assert report["spillers"] == [], (width, report)


# ----------------------------------------------------------------------- corners
def test_the_surface_is_square_except_for_the_shapes_that_are_meant_to_be_round(page):
    # theme.css resolves every radius token to 0. Anything still curved is either
    # a circle someone asked for (avatar, dot) or a radius that crept back.
    leftovers = page.evaluate("""() => {
      const wrong = [];
      document.querySelectorAll('*').forEach((node) => {
        const style = getComputedStyle(node);
        const corners = [style.borderTopLeftRadius, style.borderTopRightRadius,
                         style.borderBottomLeftRadius, style.borderBottomRightRadius];
        corners.forEach((value) => {
          if (value === '50%' || value === '0px' || value === 'auto' || !value) return;
          if (parseFloat(value) <= 0) return;
          wrong.push(node.tagName + '.' + (node.className || '') + ' ' + value);
        });
      });
      return [...new Set(wrong)];
    }""")
    assert leftovers == [], leftovers


# --------------------------------------------------------------------- controls
def test_every_control_is_a_target_a_hand_can_hit(page):
    # WCAG 2.2 AA asks for 24x24 CSS px. The page has five forms, a device list of
    # sign-out buttons and three popups, so this is the check that the compact
    # design stayed usable rather than merely compact.
    reveal_all(page)
    thin = []
    for slug in [None] + DIALOGS:
        if slug:
            open_dialog(page, slug)
        scope = f"#dialog-{slug}" if slug else "body"
        thin += page.evaluate("""(scope) => {
          const thin = [];
          document.querySelectorAll(scope + ' a, ' + scope + ' button, ' + scope + ' input, '
                                   + scope + ' select, ' + scope + ' summary').forEach((node) => {
            if (node.hidden || !node.offsetParent) return;
            const box = node.getBoundingClientRect();
            if (box.height && box.height < 24) {
              thin.push(node.tagName + '#' + (node.id || node.className) + ' h='
                        + Math.round(box.height));
            }
          });
          return thin;
        }""", scope)
        if slug:
            page.keyboard.press("Escape")
            page.wait_for_timeout(60)
    assert thin == [], thin


def test_keyboard_focus_is_visible_on_the_forms(page):
    # signin.css suppresses the outline on :focus to draw its own border, so the
    # ring has to come from :focus-visible or a keyboard user has no place-marker.
    reveal_all(page)
    reached = []
    for _ in range(14):
        page.keyboard.press("Tab")
        ring = page.evaluate("""() => {
          const node = document.activeElement;
          if (!node || node === document.body) return null;
          const style = getComputedStyle(node);
          const width = parseFloat(style.outlineWidth) || 0;
          return {tag: node.tagName, id: node.id || node.className,
                  width: width, colour: style.outlineColor};
        }""")
        if ring and ring["tag"] in {"INPUT", "BUTTON", "A", "SUMMARY"}:
            reached.append(ring)
    assert len(reached) >= 6, reached
    assert all(item["width"] >= 2 for item in reached), reached


# ---------------------------------------------------------------------- device
def test_devices_are_listed_by_name_with_the_header_underneath(page):
    open_section(page, "panel-sessions")
    titles = page.locator("#devices .device-title").evaluate_all(
        "nodes => nodes.map(node => node.textContent.trim())")
    assert titles[:4] == ["Chrome on Linux", "Safari on iPhone", "Chrome on Android",
                          "Firefox on Windows"], titles
    # A header nothing recognised does not become a confident wrong answer.
    assert "Unlisted browser" in titles, titles
    assert not any("Mozilla/5.0" in title for title in titles), titles
    # ...and the evidence is still on the row, one step down.
    assert page.locator("#devices .device-ua").count() == 4


def test_the_current_device_is_marked_and_never_offered_for_sign_out(page):
    open_section(page, "panel-sessions")
    assert page.locator("#devices li").first.locator(".this-one").count() == 1
    assert page.locator("#devices li").first.locator("button").count() == 0
    assert page.locator("#devices li").nth(1).locator("button").count() == 1


# ------------------------------------------------------------------------ text
def test_the_delete_action_sits_outside_the_grid_and_opens_its_own_popup(page):
    trigger = page.locator('.danger-zone button[data-dialog="dialog-delete-warning"]')
    shell = page.locator(".account-shell").bounding_box()
    box = trigger.bounding_box()
    assert box["y"] >= shell["y"] + shell["height"] - 1, (shell, box)
    trigger.click()
    page.wait_for_selector("#dialog-delete-warning[open]")
    page.locator('#delete-warning-continue').click()
    page.wait_for_selector("#dialog-delete-check[open]")
    assert page.locator("#form-delete-check").is_visible()
    page.keyboard.press("Escape")
    page.wait_for_timeout(80)
    assert page.locator("#dialog-delete-check").is_hidden()


def test_two_factor_states_are_said_in_the_same_words_everywhere(page):
    open_dialog(page, "totp")
    # Badges are set in small caps by CSS, so the rendered text is uppercase; the
    # point is that the strip, the option and the popup answer the same question
    # with the same words, not that any of them is spelled a particular way.
    strip = page.locator("#totp-badge").inner_text().strip().lower()
    option = page.locator("#totp-state").inner_text().strip().lower()
    popup = page.locator("#totp-dialog-state").inner_text().strip().lower()
    assert strip == option == popup == "setup waiting", (strip, option, popup)
    # A setup is already waiting in this fixture, so the popup offers the code and
    # not a second "turn it on" password box.
    assert page.locator("#enroll").is_visible()
    assert not page.locator("#totp-begin-block").is_visible()
    assert not page.locator("#totp-disable-block").is_visible()


# ------------------------------------------------------------------------ nav
PANELS = ["panel-profile", "panel-providers", "panel-sessions"]


def test_the_rail_is_a_sidebar_on_a_desk_and_a_row_on_a_phone(page, origin):
    # The reason the links moved: on a desk they stand in a column beside the section
    # they choose; under 900px there is no room beside anything, so the rail lies back
    # down across the top and the panel takes the whole width.
    for width, side_by_side in [(1280, True), (390, False)]:
        page.set_viewport_size({"width": width, "height": 900})
        page.reload(wait_until="networkidle")
        page.wait_for_timeout(120)
        boxes = page.evaluate("""() => {
          const rail = document.querySelector('.section-nav');
          const links = [...rail.querySelectorAll('a')].map((node) => node.getBoundingClientRect());
          const pane = document.querySelector('#panel-profile').getBoundingClientRect();
          const edge = rail.getBoundingClientRect();
          return {
            rail_left: edge.left, rail_right: edge.right, rail_top: edge.top,
            pane_left: pane.left, pane_top: pane.top,
            stacked: links[1].top > links[0].bottom - 1,
            position: getComputedStyle(rail).position,
          };
        }""")
        if side_by_side:
            assert boxes["position"] == "sticky", (width, boxes)
            assert boxes["stacked"], (width, boxes)
            assert boxes["rail_right"] <= boxes["pane_left"] + 1, (width, boxes)
        else:
            assert boxes["position"] == "static", (width, boxes)
            assert not boxes["stacked"], (width, boxes)
            assert boxes["rail_top"] < boxes["pane_top"], (width, boxes)
            assert abs(boxes["rail_left"] - boxes["pane_left"]) <= 1, (width, boxes)


def test_the_rail_marks_the_section_that_is_showing(page):
    active = page.locator(".section-nav a.active")
    assert active.count() == 1
    assert active.first.get_attribute("aria-current") == "true"
    assert active.first.get_attribute("href") == "#panel-profile"
    assert page.locator("#panel-profile").is_visible()


@pytest.mark.parametrize("panel_id", PANELS)
def test_choosing_a_section_shows_that_one_and_marks_it(page, panel_id):
    open_section(page, panel_id)
    assert page.locator(f"#{panel_id}").is_visible()
    assert hidden_panels(page) == [one for one in PANELS if one != panel_id]
    marked = page.locator(".section-nav a.active")
    assert marked.count() == 1
    assert marked.first.get_attribute("aria-current") == "true"
    assert marked.first.get_attribute("href") == f"#{panel_id}"
    # The address is the section's own fragment, so the back button walks the rail.
    assert page.evaluate("() => window.location.hash") == f"#{panel_id}"


def test_the_back_button_walks_the_sections(page):
    open_section(page, "panel-sessions")
    open_section(page, "panel-providers")
    page.go_back()
    page.wait_for_timeout(200)
    assert page.locator("#panel-sessions").is_visible()
    assert not page.locator("#panel-providers").is_visible()


def test_a_browser_without_javascript_still_gets_every_section(origin):
    # The rail is an enhancement, not a gate: with no script nothing is hidden and
    # every form is where it always was.
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        opened = browser.new_context(viewport={"width": 1280, "height": 900},
                                     java_script_enabled=False).new_page()
        opened.goto(f"{origin}/account", wait_until="load")
        for panel_id in PANELS:
            assert opened.locator(f"#{panel_id}").is_visible(), panel_id
        # The option links are real links, and the server renders the popup they
        # name as an ordinary block, so every field in it is reachable without a
        # script drawing it. The actions still POST JSON, which is the same
        # no-script shape the page's other forms have always had.
        for slug in DIALOGS:
            opened.goto(f"{origin}/account?step={slug}", wait_until="load")
            assert opened.locator(f"#dialog-{slug}").is_visible(), slug
            reachable = opened.evaluate("""(id) => [...document.querySelectorAll(id + ' input')]
                .filter((node) => node.offsetParent !== null).length""", f"#dialog-{slug}")
            assert reachable >= 1, slug
        # The Reset button's own link names the reset task, so that is the form the
        # server puts on the page for a browser that follows it.
        opened.goto(f"{origin}/account?step=reset", wait_until="load")
        assert opened.locator("#form-password-reset").is_visible()
        assert opened.locator("#form-password").is_hidden()
        # The delete button's link names its first popup, which is likewise rendered
        # as a block for a browser that follows it.
        opened.goto(f"{origin}/account?step=delete", wait_until="load")
        assert opened.locator("#dialog-delete-warning").is_visible()
        opened.close()
        browser.close()


def test_the_rail_offers_the_sections_in_the_order_they_are_written(page):
    # A rail that reads one way and a page that is built another is how a jump nav
    # becomes a thing you stop trusting.
    hrefs = page.locator(".section-nav a").evaluate_all(
        "nodes => nodes.map(node => node.getAttribute('href'))")
    order = page.evaluate("""() => [...document.querySelectorAll('.account-panes > .panel[id]')]
        .map((node) => '#' + node.id)""")
    assert hrefs == order, (hrefs, order)


# ---------------------------------------------------------------------- popups
@pytest.mark.parametrize("slug", DIALOGS)
def test_an_option_opens_its_popup_without_leaving_the_page(page, slug):
    page.locator(f'#panel-profile a[data-dialog="dialog-{slug}"]').first.click()
    page.wait_for_selector(f"#dialog-{slug}[open]")
    # Opening in place is the promise; the href is only the no-script fallback.
    assert page.url.endswith("/account"), page.url
    assert page.locator(f"#dialog-{slug}").is_visible()
    for other in DIALOGS:
        if other != slug:
            assert page.locator(f"#dialog-{other}").is_hidden(), other
    page.keyboard.press("Escape")
    page.wait_for_timeout(80)
    assert page.locator(f"#dialog-{slug}").is_hidden()


def test_a_click_outside_a_popup_closes_it(page):
    open_dialog(page, "password")
    page.mouse.click(4, 4)
    page.wait_for_timeout(80)
    assert page.locator("#dialog-password").is_hidden()


def test_the_close_buttons_close_the_popup_they_live_in(page):
    open_dialog(page, "email")
    page.locator("#dialog-email .dialog-head [data-dialog-close]").click()
    page.wait_for_timeout(80)
    assert page.locator("#dialog-email").is_hidden()
    # ...and the option still opens it a second time.
    open_dialog(page, "email")
    page.locator("#dialog-email .dialog-foot [data-dialog-close]").click()
    page.wait_for_timeout(80)
    assert page.locator("#dialog-email").is_hidden()


def test_a_direct_link_to_a_popup_opens_it_on_arrival(page, origin):
    # /account?step=totp is what the trigger link's href says. A page reached that
    # way must open that popup rather than render a dormant block at the bottom.
    page.goto(f"{origin}/account?step=totp", wait_until="networkidle")
    page.wait_for_timeout(120)
    assert page.evaluate("() => document.getElementById('dialog-totp').open")
    page.keyboard.press("Escape")
    page.wait_for_timeout(80)
    # The server's no-script flag was retired when the real popup opened, so the
    # closed popup does not come back as a static block under the page.
    assert page.locator("#dialog-totp").is_hidden()



def test_the_device_list_scrolls_inside_its_own_panel(page):
    # Five live sessions is five rows of a name, three lines of detail and a raw
    # header apiece, and the panel used to grow with them. The rows run out inside
    # the list now; the panel keeps one height whatever the number is.
    open_section(page, "panel-sessions")
    facts = page.evaluate("""() => {
      const list = document.getElementById('devices');
      const style = getComputedStyle(list);
      return {
        rows: list.children.length,
        scroll_height: list.scrollHeight, client_height: list.clientHeight,
        overflow: style.overflowY, overscroll: style.overscrollBehaviorY,
        last_row_shown: list.lastElementChild.getBoundingClientRect().bottom
          <= list.getBoundingClientRect().bottom + 1,
        panel_height: Math.round(document.getElementById('panel-sessions')
                                 .getBoundingClientRect().height),
      };
    }""")
    assert facts["rows"] == 5, facts
    assert facts["overflow"] == "auto", facts
    assert facts["overscroll"] == "contain", facts
    assert facts["scroll_height"] > facts["client_height"], facts
    # Proof the cap is doing something: the last row is not painted inside the box.
    assert not facts["last_row_shown"], facts
    assert facts["panel_height"] < 700, facts


# ----------------------------------------------------------------------- rhythm
LABEL_CLASHES = """(scope) => {
  const visible = (node) => node.getBoundingClientRect().width > 0
                         && node.getBoundingClientRect().height > 0;
  const clashes = [];
  for (const holder of document.querySelectorAll(scope)) {
    const nodes = [...holder.querySelectorAll('*')].filter(visible);
    for (const label of holder.querySelectorAll('form > label')) {
      if (!visible(label)) continue;
      const box = label.getBoundingClientRect();
      let closest = null;
      for (const node of nodes) {
        if (node === label || label.contains(node) || node.contains(label)) continue;
        const other = node.getBoundingClientRect();
        // Only something the label actually sits under.
        if (other.right <= box.left + 1 || other.left >= box.right - 1) continue;
        if (other.bottom <= box.top + 1 && other.bottom > (closest ? closest.bottom : -1)) {
          closest = {bottom: other.bottom, name: node.tagName + '.' + node.className};
        }
      }
      if (closest && box.top - closest.bottom < 8) {
        clashes.push({label: label.textContent.trim().slice(0, 20),
                      above: closest.name, gap: Math.round(box.top - closest.bottom)});
      }
    }
  }
  return clashes;
}"""


def test_a_label_never_lands_on_top_of_the_text_above_it(page):
    # signin.css zeroes the first label's margin because on the door pages a form
    # begins at the top of its own box. Here forms follow prose, a state row or a
    # QR, and the label was drawing over them. Measured geometrically rather than
    # as a margin, because the element above is a different one every time.
    reveal_all(page)
    assert page.evaluate(LABEL_CLASHES, ".panel") == []
    for slug in DIALOGS:
        open_dialog(page, slug)
        assert page.evaluate(LABEL_CLASHES, f"#dialog-{slug}") == [], slug
        page.keyboard.press("Escape")
        page.wait_for_timeout(60)


def test_each_password_button_opens_the_task_it_names(page):
    # Change and reset are two different tasks, so each button shows its own form
    # and only its own: stacked together they read as one action with two buttons.
    page.locator('#panel-profile a[data-dialog="dialog-password"][data-view="change"]').click()
    page.wait_for_selector("#dialog-password[open]")
    assert page.locator("#form-password").is_visible()
    assert page.locator("#form-password-reset").is_hidden()
    page.keyboard.press("Escape")
    page.wait_for_timeout(80)

    page.locator('#panel-profile a[data-dialog="dialog-password"][data-view="reset"]').click()
    page.wait_for_selector("#dialog-password[open]")
    assert page.locator("#form-password-reset").is_visible()
    assert page.locator("#form-password").is_hidden()


def test_paired_actions_share_the_line_they_were_written_for(page):
    # A full-width primary button inside a flex row pushes its partner onto a
    # second line, which is how "Cancel setup" ended up under "Confirm". The row
    # bottom-aligns, so the pair is proved by overlapping, not by matching tops.
    open_dialog(page, "totp")
    confirm = page.locator("#form-totp-confirm .row button.primary").bounding_box()
    cancel = page.locator("#totp-cancel").bounding_box()
    shared = min(confirm["y"] + confirm["height"], cancel["y"] + cancel["height"]) \
        - max(confirm["y"], cancel["y"])
    assert shared > 0, (confirm, cancel)
    assert cancel["x"] > confirm["x"] + confirm["width"], (confirm, cancel)


def test_the_primary_button_offset_is_on_the_spacing_scale(page):
    # signin.css parked every primary button 22px below its last field.
    open_dialog(page, "password")
    margin = page.evaluate("() => getComputedStyle(document.querySelector('#form-password "
                           "button.primary')).marginTop")
    assert margin in {"16px", "24px", "32px"}, margin


# ------------------------------------------------------------------- escaping
ESCAPES = """() => {
  const offenders = [];
  for (const holder of document.querySelectorAll('.panel, dialog[open]')) {
    const box = holder.getBoundingClientRect();
    const style = getComputedStyle(holder);
    const left = box.left + parseFloat(style.paddingLeft);
    const right = box.right - parseFloat(style.paddingRight);
    for (const node of holder.querySelectorAll('*')) {
      const b = node.getBoundingClientRect();
      if (b.width === 0 || b.height === 0) continue;
      if (b.right > right + 1 || b.left < left - 1) {
        offenders.push({panel: holder.id,
                        node: node.tagName.toLowerCase() + '.' + node.className,
                        over: Math.round(Math.max(b.left - left, b.right - right)),
                        text: (node.textContent || '').trim().slice(0, 34)});
      }
    }
  }
  return offenders;
}"""

# The destructive actions ask in a popup instead of rewriting their own label.
# The row button in the Devices panel is reached through the list rather than by
# id: the rows are rebuilt after every action, and the second one is a real other
# device.
DEVICE_BUTTON = "#devices li:nth-child(2) button[data-revoke]"
CONFIRMABLE = ["#disconnect-amazon", "#disconnect-youtube", "#signout-others",
               DEVICE_BUTTON]


@pytest.mark.parametrize("width", WIDTHS)
def test_nothing_escapes_the_panel_it_lives_in(origin, width):
    # Document-level overflow can hide this: a button that grows past its panel
    # overlaps the neighbour instead of widening the page, so scrollWidth stays
    # put while the layout is broken.
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        opened = browser.new_context(viewport={"width": width, "height": 900}).new_page()
        opened.goto(f"{origin}/account", wait_until="networkidle")
        # All five at once: a panel that is hidden today is the one that breaks
        # tomorrow, and the confirmation popup is opened from whichever it is.
        reveal_all(opened)
        assert opened.evaluate(ESCAPES) == [], width
        for selector in CONFIRMABLE:
            opened.locator(selector).scroll_into_view_if_needed()
            opened.click(selector)
            opened.wait_for_selector("#dialog-confirm[open]")
            escaped = opened.evaluate(ESCAPES)
            opened.keyboard.press("Escape")
            opened.wait_for_timeout(60)
            assert escaped == [], (width, selector, escaped)
        # The popups are measured the same way, one at a time: a modal lives in the
        # top layer, so two of them cannot be open together.
        for slug in DIALOGS:
            open_dialog(opened, slug)
            escaped = opened.evaluate(ESCAPES)
            opened.keyboard.press("Escape")
            opened.wait_for_timeout(60)
            assert escaped == [], (width, slug, escaped)
        opened.close()
        browser.close()


# ------------------------------------------------------------------ confirmations
CONFIRMATIONS = {
    "#disconnect-amazon": ("panel-providers", "Disconnect Amazon?", "disconnect:amazon"),
    "#disconnect-youtube": ("panel-providers", "Disconnect YouTube Music?",
                            "disconnect:youtube"),
    "#signout-others": ("panel-sessions", "Sign out other devices?", "revoke-others"),
    DEVICE_BUTTON: ("panel-sessions", "Sign out this device?", "revoke:bbbb2222"),
}


def visible_text(page, selector):
    # These labels are uppercased by CSS and wrap, so compare the words that are
    # painted rather than the source spelling.
    return " ".join(page.locator(selector).inner_text().split()).casefold()


@pytest.mark.parametrize("selector", CONFIRMATIONS)
def test_a_destructive_action_asks_in_a_popup_and_only_its_answer_acts(origin, selector):
    # Geometry cannot see this failure: the popup opens, looks satisfied and nothing
    # is ever sent. Cancel must leave the account alone, the sentence must name the
    # action actually performed, and the trigger must keep the label it started with.
    panel, title, endpoint = CONFIRMATIONS[selector]
    del POSTS[:]
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        opened = browser.new_context(viewport={"width": 1280, "height": 900}).new_page()
        opened.goto(f"{origin}/account", wait_until="networkidle")
        open_section(opened, panel)
        before = visible_text(opened, selector)

        opened.click(selector)
        opened.wait_for_selector("#dialog-confirm[open]")
        assert opened.url.split("#", 1)[0].endswith("/account"), opened.url
        assert visible_text(opened, "#dialog-confirm-title") == title.casefold()
        assert visible_text(opened, selector) == before
        if selector == DEVICE_BUTTON:
            # A modal hides the row, so the sentence has to carry the name.
            assert "safari on iphone" in visible_text(opened, "#dialog-confirm-message")
        # Cancel is the safe answer: it closes the question and sends nothing.
        opened.click("#dialog-confirm .dialog-foot [data-dialog-close]")
        opened.wait_for_timeout(80)
        assert opened.locator("#dialog-confirm").is_hidden()
        assert POSTS == []
        # The same trigger asks again, and its answer is the action.
        opened.click(selector)
        opened.wait_for_selector("#dialog-confirm[open]")
        opened.click("#dialog-confirm-yes")
        opened.wait_for_selector("#dialog-confirm", state="hidden")
        opened.wait_for_timeout(300)
        assert visible_text(opened, selector) == before
        opened.close()
        browser.close()
    assert POSTS == [endpoint], (selector, POSTS)


def test_signing_out_of_this_browser_asks_in_a_dialog_before_it_leaves(origin):
    # The header link is the one destructive control that is not a button, and it
    # sits a hand's width from "Player". It asks in a popup instead of rewriting its
    # own label; Cancel leaves the session alone, and the confirm control still
    # leaves by the href the page has always used.
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        opened = browser.new_context(viewport={"width": 1280, "height": 900}).new_page()
        opened.goto(f"{origin}/account", wait_until="networkidle")
        assert visible_text(opened, "a.signout-link") == "sign out"
        opened.click("a.signout-link")
        opened.wait_for_selector("#dialog-signout[open]")
        assert opened.url.endswith("/account"), opened.url
        assert opened.locator("#signout-confirm").get_attribute("href") == "/logout/"
        assert visible_text(opened, "a.signout-link") == "sign out"
        opened.click("#dialog-signout .dialog-foot [data-dialog-close]")
        opened.wait_for_timeout(80)
        assert opened.locator("#dialog-signout").is_hidden()
        assert opened.url.endswith("/account"), opened.url
        opened.click("a.signout-link")
        opened.wait_for_selector("#dialog-signout[open]")
        opened.click("#signout-confirm")
        opened.wait_for_timeout(500)
        assert "/logout/" in opened.url, opened.url
        opened.close()
        browser.close()


def test_a_provider_sign_in_opens_in_a_popup_on_the_account_page(origin):
    # The provider links used to be doors to a separate page. They still carry that
    # href for a browser without this script, but a click now asks for the same
    # things in place -- and Cancel must stop the background sign-in it started.
    del POSTS[:]
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        opened = browser.new_context(viewport={"width": 1280, "height": 900}).new_page()
        opened.goto(f"{origin}/account", wait_until="networkidle")
        # The fixture reports both sessions connected, so account.js hides the
        # links; a provider that is not connected is the state this test needs.
        open_section(opened, "panel-providers")
        opened.evaluate("""() => document.querySelectorAll('a[data-connect]')
            .forEach((link) => { link.hidden = false; })""")

        opened.click("#link-youtube")
        opened.wait_for_selector("#dialog-connect[open]")
        assert "/accounts/" not in opened.url, opened.url
        assert visible_text(opened, "#dialog-connect-title") == "connect youtube music"
        assert visible_text(opened, "#connect-email-label") == "google email"
        # The intro describes the form that is showing, so it is still there.
        assert opened.locator("#connect-intro").is_visible()
        opened.click("#dialog-connect .dialog-foot [data-dialog-close]")
        opened.wait_for_timeout(80)
        assert opened.locator("#dialog-connect").is_hidden()
        assert "/accounts/" not in opened.url, opened.url
        assert POSTS == [], POSTS

        opened.click("#link-amazon")
        opened.wait_for_selector("#dialog-connect[open]")
        assert visible_text(opened, "#dialog-connect-title") == "connect amazon"
        assert visible_text(opened, "#connect-email-label") == "amazon email or mobile number"
        assert opened.locator("#connect-intro").is_visible()
        opened.fill("#connect_email", "person@example.test")
        opened.fill("#connect_password", "hunter2-hunter2")
        opened.click("#connect-submit")
        opened.wait_for_selector("#connect-approval-panel:not([hidden])")
        assert opened.locator("#connect_email").input_value() == "person@example.test"
        assert opened.locator("#connect_password").input_value() == ""
        # The provider is asking a question now, so the sentence about signing in
        # is behind the person rather than stacked over the question.
        assert opened.locator("#connect-intro").is_hidden()
        # Closing while the provider waits on a phone approval must not leave that
        # background browser running on the server.
        opened.click("#dialog-connect .dialog-foot [data-dialog-close]")
        opened.wait_for_timeout(300)
        assert opened.locator("#dialog-connect").is_hidden()
        assert POSTS == ["amazon:start", "amazon:cancel"], POSTS
        opened.close()
        browser.close()


def test_a_finished_provider_sign_in_closes_the_popup_and_says_so_in_place(origin):
    # The popup exists so a connection does not cost the page the person was on.
    # Answering the provider's last question has to close it, leave the URL alone
    # and repaint the rows, not navigate to a result page.
    del POSTS[:]
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        opened = browser.new_context(viewport={"width": 1280, "height": 900}).new_page()
        opened.goto(f"{origin}/account", wait_until="networkidle")
        open_section(opened, "panel-providers")
        opened.evaluate("""() => {
          const link = document.getElementById('link-youtube');
          link.hidden = false;
        }""")
        opened.click("#link-youtube")
        opened.wait_for_selector("#dialog-connect[open]")
        opened.fill("#connect_email", "person@example.test")
        opened.fill("#connect_password", "hunter2-hunter2")
        opened.click("#connect-submit")
        opened.wait_for_selector("#connect-code-panel:not([hidden])")
        opened.fill("#connect_code", "123456")
        opened.click("#connect-code-submit")
        opened.wait_for_selector("#dialog-connect", state="hidden")
        opened.wait_for_timeout(400)
        assert "/accounts/" not in opened.url, opened.url
        assert "youtube music connected" in visible_text(opened, "#notes-providers")
        assert POSTS == ["youtube:start", "youtube:code"], POSTS
        opened.close()
        browser.close()


def dialog_fit(page):
    """The open popup's own edges and anything painting outside its body box."""
    return page.evaluate("""() => {
      const doc = document.documentElement;
      const box = document.querySelector('dialog[open]');
      const body = box.querySelector('.dialog-body');
      const rect = body.getBoundingClientRect();
      const style = getComputedStyle(body);
      const left = rect.left + parseFloat(style.paddingLeft);
      const right = rect.right - parseFloat(style.paddingRight);
      const spills = [];
      body.querySelectorAll('*').forEach((node) => {
        const b = node.getBoundingClientRect();
        if (!b.width || !b.height) return;
        if (b.right > right + 1 || b.left < left - 1) {
          spills.push(node.tagName.toLowerCase() + '.' + node.className + ' over='
                      + Math.round(Math.max(b.left - left, b.right - right)));
        }
      });
      const edge = box.getBoundingClientRect();
      return {
        scroll: doc.scrollWidth - doc.clientWidth,
        dialog_right: Math.round(edge.right), viewport: doc.clientWidth,
        spills: [...new Set(spills)],
      };
    }""")


def thin_targets(page):
    return page.evaluate("""() => {
      const thin = [];
      document.querySelectorAll('dialog[open] a, dialog[open] button, '
                               + 'dialog[open] input, dialog[open] select, '
                               + 'dialog[open] summary').forEach((node) => {
        if (node.hidden || !node.offsetParent) return;
        const box = node.getBoundingClientRect();
        if (box.height && box.height < 24) {
          thin.push(node.tagName + '#' + (node.id || node.className) + ' h='
                    + Math.round(box.height));
        }
      });
      return thin;
    }""")


@pytest.mark.parametrize("width", WIDTHS)
def test_the_connect_popup_fits_every_width_and_keeps_24px_targets(origin, width):
    # A provider image and radio rows are where a dialog grows past a phone or
    # shrinks a control below a hand. The credentials form, the choice panel (the
    # only one with radios) and the approval panel are measured separately, because
    # each replaces the one before it.
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        opened = browser.new_context(viewport={"width": width, "height": 900}).new_page()
        opened.goto(f"{origin}/account", wait_until="networkidle")
        open_section(opened, "panel-providers")
        opened.evaluate("""() => document.querySelectorAll('a[data-connect]')
            .forEach((link) => { link.hidden = false; })""")
        opened.click("#link-amazon")
        opened.wait_for_selector("#dialog-connect[open]")

        fit = dialog_fit(opened)
        assert fit["scroll"] <= 1, (width, "credentials", fit)
        assert fit["dialog_right"] <= fit["viewport"] + 1, (width, "credentials", fit)
        assert fit["spills"] == [], (width, "credentials", fit)
        assert thin_targets(opened) == [], (width, "credentials", thin_targets(opened))

        opened.fill("#connect_email", "choice@example.test")
        opened.fill("#connect_password", "hunter2-hunter2")
        opened.click("#connect-submit")
        opened.wait_for_selector("#connect-choice-panel:not([hidden])")
        assert opened.locator("#connect-intro").is_hidden()
        assert opened.locator(".connect-choice input").count() == 2
        fit = dialog_fit(opened)
        assert fit["scroll"] <= 1, (width, "choice", fit)
        assert fit["dialog_right"] <= fit["viewport"] + 1, (width, "choice", fit)
        assert fit["spills"] == [], (width, "choice", fit)
        assert thin_targets(opened) == [], (width, "choice", thin_targets(opened))

        opened.click("#connect-choice-submit")
        opened.wait_for_selector("#connect-approval-panel:not([hidden])")
        fit = dialog_fit(opened)
        assert fit["scroll"] <= 1, (width, "approval", fit)
        assert fit["dialog_right"] <= fit["viewport"] + 1, (width, "approval", fit)
        assert fit["spills"] == [], (width, "approval", fit)
        assert thin_targets(opened) == [], (width, "approval", thin_targets(opened))
        opened.close()
        browser.close()


def test_the_sign_in_links_keep_the_standalone_page_as_their_fallback(origin):
    # The popup is an enhancement. A browser without the script follows the href to
    # the page that has always served this flow (test_account_pages.py proves the
    # route answers, test_account_signin.py drives the page), and a modifier-click
    # leaves that door to the browser instead of catching it in the page handler.
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        opened = browser.new_context(viewport={"width": 1280, "height": 900}).new_page()
        opened.goto(f"{origin}/account", wait_until="networkidle")
        assert opened.locator("#link-amazon").get_attribute("href") == "/accounts/amazon"
        assert opened.locator("#link-youtube").get_attribute("href") == "/accounts/youtube"
        open_section(opened, "panel-providers")
        opened.evaluate("""() => document.querySelectorAll('a[data-connect]')
            .forEach((link) => { link.hidden = false; })""")
        opened.click("#link-amazon", modifiers=["Control"])
        opened.wait_for_timeout(200)
        assert opened.locator("#dialog-connect").is_hidden()
        assert opened.url.split("#", 1)[0].endswith("/account"), opened.url
        opened.close()
        browser.close()


# ---------------------------------------------------------------- light theme
def test_the_state_colours_survive_a_light_surface(page):    # /account is a dark surface today, but theme.css promises the tokens carry a
    # light theme too, and admin.css already uses it. Tint ink that was chosen for
    # near-black has to be swapped, or the panel goes unreadable at #ffffff.
    page.evaluate("() => document.documentElement.setAttribute('data-theme', 'light')")
    page.wait_for_timeout(60)
    samples = page.evaluate("""() => {
      const numbers = (value) => (value.match(/[\\d.]+/g) || [0, 0, 0, 0]).map(Number);
      const toRgb = (value) => {
        const parts = numbers(value);
        return {r: parts[0], g: parts[1], b: parts[2], a: parts.length > 3 ? parts[3] : 1};
      };
      // A badge's own background is a 12% wash; what the ink actually sits on is
      // that wash composited over the panel, over the page. Comparing the ink to
      // the raw wash colour measures a colour nobody sees.
      const over = (top, bottom) => {
        const a = top.a + bottom.a * (1 - top.a);
        if (a === 0) return {r: 255, g: 255, b: 255, a: 1};
        return {r: (top.r * top.a + bottom.r * bottom.a * (1 - top.a)) / a,
                g: (top.g * top.a + bottom.g * bottom.a * (1 - top.a)) / a,
                b: (top.b * top.a + bottom.b * bottom.a * (1 - top.a)) / a, a};
      };
      const backdrop = (node) => {
        const layers = [];
        let walk = node;
        let opaque = false;
        while (walk) {
          const colour = toRgb(getComputedStyle(walk).backgroundColor);
          if (colour.a > 0) layers.push(colour);
          if (colour.a === 1) { opaque = true; break; }
          walk = walk.parentElement;
        }
        // Only when nothing in the chain was ever opaque: a page that paints its
        // own background has already answered the question, and appending white
        // on top of the answer would measure the wrong surface.
        if (!opaque) layers.push({r: 255, g: 255, b: 255, a: 1});
        // Collected from the node outward, so each successive layer sits *below*
        // the one before it: reversed, the deepest goes down first and the colour
        // the ink actually sits on is painted last.
        return layers.reverse().reduce((below, above) => over(above, below));
      };
      const luminance = (colour) => {
        const [r, g, b] = [colour.r, colour.g, colour.b].map((v) => {
          const c = v / 255;
          return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
        });
        return 0.2126 * r + 0.7152 * g + 0.0722 * b;
      };
      const read = (selector) => {
        const node = document.querySelector(selector);
        if (!node) return null;
        const ink = luminance(toRgb(getComputedStyle(node).color));
        const back = luminance(backdrop(node));
        const lighter = Math.max(ink, back), darker = Math.min(ink, back);
        return {selector, ratio: Number(((lighter + 0.05) / (darker + 0.05)).toFixed(2))};
      };
      // The prose, the state badges and the destructive affordances are the text a
      // person has to be able to read; the accent-filled primary button is brand,
      // and its ink-to-fill ratio is a property of #e8590c rather than of this page.
      return ['.badge.bad', '.badge.warn', '.badge.ok', '.signout-link',
              '.danger-zone a', '.field-help', '.chip-label',
              '.devices .device-meta', '.state-line .label',
              '.section-nav a', 'body.account button.primary'].map(read).filter(Boolean);
    }""")
    brand_fill = "body.account button.primary"
    failing = [item for item in samples
               if item["ratio"] < (3.0 if item["selector"] == brand_fill else 4.5)]
    assert failing == [], failing


def palette_facts(page):
    return page.evaluate("""() => ({
      attribute: document.documentElement.getAttribute('data-theme'),
      canvas: getComputedStyle(document.body).backgroundColor,
      ink: getComputedStyle(document.querySelector('#head-since')).color,
      card: getComputedStyle(document.querySelector('.panel')).backgroundColor,
      scheme: document.querySelector('meta[name="color-scheme"]').content,
      bar: document.querySelector('meta[name="theme-color"]').content,
    })""")


def test_the_light_palette_reaches_the_page_that_inherits_it(page, origin):
    # An attribute no stylesheet reads is indistinguishable from no attribute at all,
    # so this checks the canvas actually moves. The palette is a stored preference
    # read at render time, not a control on this page.
    page.goto(f"{origin}/account?theme=light", wait_until="networkidle")
    page.wait_for_timeout(120)
    facts = palette_facts(page)
    assert facts["attribute"] == "light"
    assert facts["canvas"] == "rgb(243, 243, 245)"
    assert facts["card"] == "rgb(255, 255, 255)"
    # The hint's own ink: --muted, which is #5b5b66 on a light canvas and #a8a8b0
    # on a dark one. Either value proves the token block was swapped.
    assert facts["ink"] == "rgb(91, 91, 102)"
    assert facts["scheme"] == "light dark"
    assert facts["bar"] == "#f3f3f5"


def test_the_dark_render_is_the_one_the_page_has_always_shown(page, origin):
    facts = palette_facts(page)
    assert facts["attribute"] == "dark"
    assert facts["canvas"] == "rgb(16, 16, 19)"
    assert facts["card"] == "rgb(27, 27, 31)"
    assert facts["ink"] == "rgb(168, 168, 176)"
    assert facts["scheme"] == "dark"
    assert facts["bar"] == "#101013"


def test_the_page_offers_no_palette_switch_of_its_own(page):
    # One preference, one control for it. The administrator panel already holds the
    # theme switch for this account; a second one here was a second thing to disagree
    # with it, and the account page is not where a person came to choose a colour.
    assert page.locator("#form-theme").count() == 0
    assert "switch to" not in page.locator(".account-actions-top").inner_text().lower()


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_the_header_links_keep_the_line_they_were_written_for(page, origin, theme):
    # Two links, one line, one height: the row is a control family, and a control that
    # drops below its neighbour or grows taller than it is the shape this page produces
    # when a label wraps.
    page.goto(f"{origin}/account?theme={theme}", wait_until="networkidle")
    page.wait_for_timeout(120)
    row = page.evaluate("""() => {
      const boxes = [...document.querySelectorAll('.account-actions-top a')]
        .map((node) => {
          const b = node.getBoundingClientRect();
          return {text: node.textContent.trim().slice(0, 24), top: b.top, bottom: b.bottom,
                  height: b.height, width: b.width};
        });
      const line = boxes[0];
      return {
        count: boxes.length,
        same_line: boxes.every((box) => box.top < line.bottom && box.bottom > line.top),
        equal_height: new Set(boxes.map((box) => Math.round(box.height))).size,
        widest: Math.max(...boxes.map((box) => box.width)),
        boxes,
      };
    }""")
    assert row["count"] == 2, row["boxes"]
    assert row["same_line"], row["boxes"]
    assert row["equal_height"] == 1, row["boxes"]
    assert row["widest"] >= 24, row["boxes"]


@pytest.mark.parametrize('width', [320, 390, 1440])
def test_services_has_no_echo_code_pairing_controls(page, origin, width):
    at_width(page, origin, width)
    open_section(page, 'panel-providers')
    expect(page.locator('#disconnect-amazon')).to_be_visible()
    assert page.locator('#alexa-link-code, #alexa-unlink, #alexa-link-result, .echo-pairing').count() == 0
    assert 'Generate pairing code' not in page.locator('body').inner_text()
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')


@pytest.mark.parametrize('failure', ['network', 'html'])
def test_password_failure_stays_visible_in_dialog_without_erasing_input(page, failure):
    def reject(route):
        if failure == 'network':
            route.abort()
        else:
            route.fulfill(status=502, content_type='text/html', body='<h1>Bad gateway</h1>')
    page.route('**/api/account/password/reset', reject)
    open_dialog(page, 'password')
    form = page.locator('#form-password')
    form.locator('#change-code').fill('123456')
    form.get_by_role('button', name='Verify and continue', exact=True).click()
    form.locator('[name=password]').fill('New!Password123')
    form.locator('[name=confirm_password]').fill('New!Password123')
    form.get_by_role('button', name='Change password', exact=True).click()
    expect(page.locator('#dialog-password')).to_be_visible()
    expect(page.locator('#dialog-password .toast')).to_contain_text(
        'Could not connect' if failure == 'network' else 'unreadable response')
    expect(form.locator('[name=password]')).to_have_value('New!Password123')
    expect(form.get_by_role('button', name='Change password', exact=True)).to_be_enabled()


def test_disabled_amazon_access_hides_service_and_status_without_hiding_youtube(page, origin):
    import json
    disabled = dict(FIXTURE, alexa_access_enabled=False,
                    providers=[row for row in FIXTURE['providers'] if row['provider'] != 'amazon'],
                    provider_map={'youtube': FIXTURE['provider_map']['youtube']})
    page.route('**/api/account', lambda route: route.fulfill(status=200, content_type='application/json', body=json.dumps(disabled)))
    page.route('**/api/profile_status/**', lambda route: route.fulfill(status=200, content_type='application/json', body=json.dumps({'alexa_access_enabled': False, 'youtube_own_session': True})))
    page.goto(origin + '/account')
    open_section(page, 'panel-providers')
    expect(page.locator('#service-amazon')).to_be_hidden()
    expect(page.locator('#service-youtube')).to_be_visible()
    expect(page.locator('#sum-services')).to_contain_text('Linked')


@pytest.mark.parametrize('identifier', ['another-amazon-account@example.test', '+919876543210'])
def test_amazon_popup_accepts_an_independent_email_or_mobile_number(page, origin, identifier):
    page.goto(origin + '/account')
    open_section(page, 'panel-providers')
    page.locator('#link-amazon').evaluate('(node) => node.hidden = false')
    page.click('#link-amazon')
    expect(page.locator('#connect_email')).to_have_attribute('type', 'text')
    expect(page.locator('#connect-intro')).to_contain_text('Your Music Box email can differ')
    page.fill('#connect_email', identifier)
    page.fill('#connect_password', 'Synthetic!Password12')
    with page.expect_request('**/alexa/form_login/') as request:
        page.click('#connect-submit')
    assert request.value.post_data_json['email'] == identifier
    expect(page.locator('#connect-approval-panel')).to_be_visible()
    page.locator('#dialog-connect .dialog-foot [data-dialog-close]').click()
    expect(page.locator('#dialog-connect')).not_to_be_visible()


def test_deletion_sends_nothing_until_explicit_send_otp_click(page):
    POSTS.clear()
    page.locator('.danger-zone button').click()
    assert page.locator('#dialog-delete-warning').is_visible()
    assert POSTS == []
    page.locator('#delete-warning-continue').click()
    page.locator('#form-delete-check input[name=current_password]').fill('test-password')
    page.locator('#del_username').fill(FIXTURE['username'])
    page.locator('button[form=form-delete-check]').click()
    page.locator('#delete-confirm-yes').click()
    expect(page.locator('#dialog-delete-code')).to_be_visible()
    assert POSTS == ['delete:check']
    assert page.locator('#delete_code').is_hidden()
    page.locator('#delete-send-code').click()
    expect(page.locator('#delete_code')).to_be_visible()
    expect(page.locator('#delete-send-code')).to_be_disabled()
    expect(page.locator('#form-delete-code .code-timer')).to_contain_text('Code expires in')
    assert POSTS == ['delete:check', 'reauth:send']

@pytest.mark.parametrize('width', [390, 1280])
def test_provider_popup_centers_loading_and_offers_amazon_recovery_after_failure(page, origin, width):
    page.set_viewport_size({'width': width, 'height': 900})
    page.goto(origin + '/account')
    open_section(page, 'panel-providers')
    page.locator('#link-amazon').evaluate('(node) => node.hidden = false')
    page.click('#link-amazon')
    expect(page.locator('#connect-fallback')).not_to_be_visible()
    pending = []
    page.route('**/alexa/form_login/', lambda route: pending.append(route))
    page.fill('#connect_email', 'person@example.test')
    page.fill('#connect_password', 'synthetic-password')
    page.click('#connect-submit')
    expect(page.locator('#connect-working')).to_be_visible()
    expect(page.locator('#connect-setup')).not_to_be_visible()
    page.wait_for_timeout(50)
    assert len(pending) == 1
    pending[0].fulfill(json={'state': 'error', 'message': 'The security picture could not load.'})
    expect(page.locator('#connect-working')).not_to_be_visible()
    expect(page.locator('#connect-fallback')).to_be_visible()
    expect(page.locator('#connect-retry')).to_be_visible()
    expect(page.locator('#connect-fallback')).to_have_attribute('href', '/accounts/amazon?interactive=1')
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
