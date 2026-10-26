"""Pixel contract for /account, in real Chromium at the widths people use.

The page is a settings surface with six forms, a status strip, a jump nav and a
device list -- the kind of layout that breaks quietly: a panel that no longer
fits its column, a nav row that starts scrolling the document sideways, a control
that becomes a 19px tap target, a border radius that came back. None of that is
visible in a template diff, and all of it is visible here.

Everything asserted is measurable, and the fixtures are deliberately the awkward
shape of each state: an unconfirmed address with a live code, a waiting two-factor
enrollment, two linked providers, five sessions with real headers, and long
values that a name column would otherwise clip.
"""
from pathlib import Path
import threading

import pytest
from flask import Flask, Response, jsonify, render_template, request
from werkzeug.serving import make_server
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
WIDTHS = [320, 375, 390, 768, 1024, 1440, 1920]
# What the page actually sent to the server, recorded by the stubs below. The
# layout checks are blind to a button that only ever arms, so the destructive
# actions are the one thing here worth counting in requests.
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
}


@pytest.fixture(scope="module")
def origin():
    app = Flask(__name__, template_folder=str(ROOT / "templates"),
                static_folder=str(ROOT / "templates/static"), static_url_path="/static")
    app.config["TEMPLATES_AUTO_RELOAD"] = True

    @app.route("/account")
    def account_page():
        # ?theme=light renders the same page in the other palette; the real route
        # reads the stored preference instead of a query string.
        wanted = request.args.get("theme")
        return render_template("account.html", app_name="Music Box", csrf_form="test-token",
                               asset_url=lambda relative: f"/static/{relative}?v=fixture",
                               theme=wanted if wanted in ("dark", "light") else "dark",
                               **FIXTURE)

    @app.route("/api/account")
    def api_account():
        # account.js reloads this after every action; the layout checks click
        # through a panel, so the endpoint has to answer like the real one.
        return jsonify(FIXTURE)

    @app.route("/api/profile_status/")
    def profile_status():
        return jsonify({"amazon_connected": True, "youtube_own_session": True,
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

    @app.route("/api/account/sessions/revoke", methods=["POST"])
    def sessions_revoke():
        body = request.get_json(silent=True) or {}
        POSTS.append("revoke-others" if body.get("others") else f"revoke:{body.get('id')}")
        return jsonify({})

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


def hidden_panels(page):
    return page.evaluate("""() => [...document.querySelectorAll('.account-panes > .panel')]
        .filter((node) => node.hidden).map((node) => node.id)""")


def reveal_all(page):
    """Put every section back on the page -- the shape a browser with no JavaScript
    gets -- so a geometry sweep measures all five panels rather than whichever one
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
    # WCAG 2.2 AA asks for 24x24 CSS px. The page has six forms and a device list
    # of sign-out buttons, so this is the check that the compact design stayed
    # usable rather than merely compact.
    reveal_all(page)
    small = page.evaluate("""() => {
      const thin = [];
      document.querySelectorAll('a, button, input, select, summary').forEach((node) => {
        if (node.hidden || !node.offsetParent) return;
        const box = node.getBoundingClientRect();
        if (box.height && box.height < 24) {
          thin.push(node.tagName + '#' + (node.id || node.className) + ' h=' + Math.round(box.height));
        }
      });
      return thin;
    }""")
    assert small == [], small


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
def test_prose_is_capped_to_a_measure_wider_than_the_panel(page):
    # A section that is the only thing on the page is not a licence for a sentence
    # that runs the width of it.
    open_section(page, "panel-security")
    width = page.evaluate("() => document.querySelector('#panel-security .panel-hint') "
                          ".getBoundingClientRect().width")
    assert width < 720, width


def test_two_factor_states_are_said_in_the_same_words_everywhere(page):
    open_section(page, "panel-security")
    # Badges are set in small caps by CSS, so the rendered text is uppercase; the
    # point is that the glance row and the panel answer the same question with the
    # same words, not that either of them is spelled a particular way.
    strip = page.locator("#totp-badge").inner_text().strip().lower()
    heading = page.locator("#totp-state").inner_text().strip().lower()
    assert strip == heading == "setup waiting", (strip, heading)
    assert page.locator("#totp-off").is_visible()
    assert not page.locator("#totp-on").is_visible()
    assert page.locator("#enroll").is_visible()


# ------------------------------------------------------------------------ nav
PANELS = ["panel-profile", "panel-providers", "panel-sessions", "panel-security",
          "panel-delete"]


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
    # The address is the section's own fragment, so the back button walks the rail and
    # /account#panel-security keeps meaning what it always meant.
    assert page.evaluate("() => window.location.hash") == f"#{panel_id}"


def test_the_back_button_walks_the_sections(page):
    open_section(page, "panel-sessions")
    open_section(page, "panel-security")
    page.go_back()
    page.wait_for_timeout(200)
    assert page.locator("#panel-sessions").is_visible()
    assert not page.locator("#panel-security").is_visible()


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


# ------------------------------------------------------------------ device list
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
def test_a_label_never_lands_on_top_of_the_text_above_it(page):
    # signin.css zeroes the first label's margin because on the door pages a form
    # begins at the top of its own box. Here forms follow prose, a state row or a
    # QR, and the label was drawing over them. Measured geometrically rather than
    # as a margin, because the element above is a different one every time.
    reveal_all(page)
    tight = page.evaluate("""() => {
      const visible = (node) => node.getBoundingClientRect().width > 0
                             && node.getBoundingClientRect().height > 0;
      const clashes = [];
      for (const panel of document.querySelectorAll('.panel')) {
        const nodes = [...panel.querySelectorAll('*')].filter(visible);
        for (const label of panel.querySelectorAll('form > label')) {
          if (!visible(label)) continue;
          const box = label.getBoundingClientRect();
          let closest = null;
          for (const node of nodes) {
            if (node === label || label.contains(node) || node.contains(label)) continue;
            const other = node.getBoundingClientRect();
            // Only something the label actually sits under. The security panel is
            // two columns, and a paragraph in the other one shares the row.
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
    }""")
    assert tight == [], tight


def test_a_second_form_in_a_panel_is_separated_from_the_first(page):
    # "Save changes" and "Confirm my email" are two different tasks. Stacked with
    # no break between them they read as one action with two buttons.
    save = page.locator("#form-profile button.primary").bounding_box()
    verify = page.locator("#form-verify-code").bounding_box()
    border = page.evaluate("() => getComputedStyle(document.querySelector('#form-verify-code'))"
                           ".borderTopWidth")
    assert border == "1px", border
    gap = verify["y"] - (save["y"] + save["height"])
    assert 16 <= gap <= 32, gap


def test_paired_actions_share_the_line_they_were_written_for(page):
    # A full-width primary button inside a flex row pushes its partner onto a
    # second line, which is how "Cancel setup" ended up under "Confirm". The row
    # bottom-aligns, so the pair is proved by overlapping, not by matching tops.
    open_section(page, "panel-security")
    confirm = page.locator("#form-totp-confirm .row button.primary").bounding_box()
    cancel = page.locator("#totp-cancel").bounding_box()
    shared = min(confirm["y"] + confirm["height"], cancel["y"] + cancel["height"]) \
        - max(confirm["y"], cancel["y"])
    assert shared > 0, (confirm, cancel)
    assert cancel["x"] > confirm["x"] + confirm["width"], (confirm, cancel)


def test_the_primary_button_offset_is_on_the_spacing_scale(page):
    # signin.css parked every primary button 22px below its last field.
    margin = page.evaluate("() => getComputedStyle(document.querySelector('#form-profile "
                           "button.primary')).marginTop")
    assert margin in {"16px", "24px", "32px"}, margin


# ------------------------------------------------------------------- escaping
ESCAPES = """() => {
  const offenders = [];
  for (const panel of document.querySelectorAll('.panel')) {
    const box = panel.getBoundingClientRect();
    const style = getComputedStyle(panel);
    const left = box.left + parseFloat(style.paddingLeft);
    const right = box.right - parseFloat(style.paddingRight);
    for (const node of panel.querySelectorAll('*')) {
      const b = node.getBoundingClientRect();
      if (b.width === 0 || b.height === 0) continue;
      if (b.right > right + 1 || b.left < left - 1) {
        offenders.push({panel: panel.id,
                        node: node.tagName.toLowerCase() + '.' + node.className,
                        over: Math.round(Math.max(b.left - left, b.right - right)),
                        text: (node.textContent || '').trim().slice(0, 34)});
      }
    }
  }
  return offenders;
}"""

# The actions that rewrite their own label with a sentence when armed.
ARMABLE = ["#disconnect-amazon", "#disconnect-youtube", "#signout-others",
           "#devices li:nth-child(2) button[data-revoke]"]


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
        # tomorrow, and the armed labels are clicked inside whichever it is.
        reveal_all(opened)
        assert opened.evaluate(ESCAPES) == [], width
        for selector in ARMABLE:
            opened.locator(selector).scroll_into_view_if_needed()
            opened.click(selector)
            opened.wait_for_timeout(60)
        escaped = opened.evaluate(ESCAPES)
        opened.close()
        browser.close()
        assert escaped == [], (width, escaped)


# ------------------------------------------------------------------ two-tap actions
# The row button in the Devices panel, reached through the list rather than by id:
# the rows are rebuilt after every action, and the second one is a real other device.
DEVICE_BUTTON = "#devices li:nth-child(2) button[data-revoke]"

ARMED = {
    "#disconnect-amazon": ("panel-providers", "Disconnect", "Tap again to disconnect Amazon",
                           "disconnect:amazon"),
    "#disconnect-youtube": ("panel-providers", "Disconnect",
                            "Tap again to disconnect YouTube Music", "disconnect:youtube"),
    "#signout-others": ("panel-sessions", "Sign out other devices",
                        "Tap again to sign out every other device", "revoke-others"),
    # A row's own button arms with "this device" because the line beside it already
    # says which device that is.
    DEVICE_BUTTON: ("panel-sessions", "Sign out", "Tap again to sign out this device",
                    "revoke:bbbb2222"),
}


def visible_text(page, selector):
    # These labels are uppercased by CSS and wrap when armed, so compare the words
    # that are painted rather than the source spelling.
    return " ".join(page.locator(selector).inner_text().split()).casefold()


@pytest.mark.parametrize("selector", ARMED)
def test_the_second_tap_actually_does_the_thing_the_first_one_armed_for(origin, selector):
    # Geometry cannot see this failure: the label changes, the panel looks satisfied
    # and nothing is ever sent. It is also the only check that the sentence the button
    # arms with still names the same action the click performs.
    panel, label, armed, endpoint = ARMED[selector]
    del POSTS[:]
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        opened = browser.new_context(viewport={"width": 1280, "height": 900}).new_page()
        opened.goto(f"{origin}/account", wait_until="networkidle")
        open_section(opened, panel)
        assert visible_text(opened, selector) == label.casefold()
        opened.click(selector)
        assert visible_text(opened, selector) == armed.casefold()
        assert POSTS == []
        opened.click(selector)
        opened.wait_for_timeout(700)
        assert visible_text(opened, selector) == label.casefold()
        opened.close()
        browser.close()
    assert POSTS == [endpoint], (selector, POSTS)


def test_signing_out_of_this_browser_asks_once_before_it_leaves(origin):
    # The header link is the one destructive control that is not a button, and it sits
    # a hand's width from "Player". The first tap asks, the second one leaves -- and it
    # leaves by the href, so the sign-out route is the one the page has always used.
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        opened = browser.new_context(viewport={"width": 1280, "height": 900}).new_page()
        opened.goto(f"{origin}/account", wait_until="networkidle")
        assert visible_text(opened, "a.signout-link") == "sign out"
        opened.click("a.signout-link")
        assert opened.url.endswith("/account"), opened.url
        assert visible_text(opened, "a.signout-link") == "tap again to sign out"
        opened.click("a.signout-link")
        opened.wait_for_timeout(500)
        assert "/logout/" in opened.url, opened.url
        opened.close()
        browser.close()


# ---------------------------------------------------------------- light theme
def test_the_state_colours_survive_a_light_surface(page):
    # /account is a dark surface today, but theme.css promises the tokens carry a
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
              '#panel-delete summary', '.panel-hint', '.field-help', '.chip-label',
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
      ink: getComputedStyle(document.querySelector('.panel-hint')).color,
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
