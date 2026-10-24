"""Real Chromium hit testing for the desktop timeline's transparent target."""
import os
from pathlib import Path
from playwright.sync_api import sync_playwright

STATIC = Path(__file__).resolve().parents[2] / 'templates' / 'static'


def test_desktop_timeline_accepts_clicks_above_and_below_the_painted_strip():
    with sync_playwright() as driver:
        browser = driver.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'))
        page = browser.new_page(viewport={'width': 1440, 'height': 900})
        page.set_content('''<section class="player-section is-visible" style="--playbar-h:72px"><div class="progress" id="progress">
            <div class="progress-track" id="progress-track"><div id="progress-fill" class="progress-fill"></div></div>
            </div></section>''')
        page.add_style_tag(path=str(STATIC / 'css/player.css'))
        page.evaluate('''() => {
            window.hits = [];
            document.querySelector('#progress-track').addEventListener('mousedown', e => hits.push(e.clientX));
        }''')
        box = page.locator('#progress-track').bounding_box()
        assert box and box['height'] <= 4  # Hit target must not thicken the visible timeline.
        x, y = box['x'] + box['width'] / 2, box['y'] + box['height'] / 2
        page.mouse.click(x, y - 8)
        page.mouse.click(x, y + 8)
        assert len(page.evaluate('hits')) == 2
        page.mouse.click(x, y - 15)
        assert len(page.evaluate('hits')) == 2  # Do not swallow unrelated controls.
        browser.close()
