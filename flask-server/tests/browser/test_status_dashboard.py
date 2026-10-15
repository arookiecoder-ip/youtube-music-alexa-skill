"""Real Chromium checks against the Flask status page. No external services."""
from contextlib import ExitStack
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
from unittest import mock
import pytest

playwright = pytest.importorskip('playwright.sync_api')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from test_next_track import server
from status_monitor import Metrics, CachedCollector
from werkzeug.serving import make_server


@pytest.fixture(scope='module')
def live_server():
    with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
        root = Path(directory)
        (root/'AAAAAAAAAAA.m4a').write_bytes(b'a'*4096)
        (root/'BBBBBBBBBBB.webm').write_bytes(b'b'*2048)
        (root/'BBBBBBBBBBB.warmup').write_bytes(b'')
        (root/'CCCCCCCCCCC.part').write_bytes(b'c'*128)
        database = root/'state.db'
        with sqlite3.connect(database) as conn: conn.execute('create table fixture (id integer)')
        for name, value in [('DB_FILE', str(database)), ('AUDIO_CACHE_DIR', directory), ('_operational', Metrics()), ('_status_storage', CachedCollector())]:
            stack.enter_context(mock.patch.object(server, name, value))
        stack.enter_context(mock.patch.object(server.Supporting, 'get_ytdlp_cookies_file', return_value=None))
        stack.enter_context(mock.patch.object(server, '_ensure_db'))
        server.Supporting.cached_audio_path('AAAAAAAAAAA')
        server.Supporting.cached_audio_path('DDDDDDDDDDD')
        app = make_server('127.0.0.1', 0, server.app, threaded=True)
        worker = threading.Thread(target=app.serve_forever, daemon=True); worker.start()
        try: yield f'http://127.0.0.1:{app.server_port}'
        finally: app.shutdown(); worker.join(timeout=2); app.server_close()


@pytest.fixture()
def browser_page(live_server):
    with playwright.sync_playwright() as driver:
        browser = driver.chromium.launch(headless=True)
        context = browser.new_context(viewport={'width':1440,'height':1050}, extra_http_headers={'X-Api-Key':server.API_KEY})
        page = context.new_page()
        errors = []; page.on('pageerror', lambda error: errors.append(str(error)))
        yield page, live_server, errors
        context.close(); browser.close()


def screenshot(page, name, fallback):
    directory = Path(os.environ.get('STATUS_SCREENSHOT_DIR', str(fallback)))
    directory.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(directory / name), full_page=True)


def ready(page, base):
    page.goto(base+'/status')
    playwright.expect(page.locator('#updated')).to_contain_text('Updated')
    playwright.expect(page.locator('#inventory')).to_contain_text('AAAAAAAAAAA')


def test_desktop_filters_pause_export_manual_cookie_probe_and_no_javascript_errors(browser_page, tmp_path):
    page, base, errors = browser_page
    ready(page, base)
    playwright.expect(page.locator('#hit-rate')).to_have_text('50%')
    page.locator('#pool').select_option('warmup')
    playwright.expect(page.locator('#inventory tr')).to_have_count(1)
    playwright.expect(page.locator('#inventory')).to_contain_text('BBBBBBBBBBB')
    page.locator('#pool').select_option('partial')
    playwright.expect(page.locator('#inventory')).to_contain_text('CCCCCCCCCCC')
    page.locator('#search').fill('not-matching')
    playwright.expect(page.locator('#inventory')).to_contain_text('No matching data')
    page.locator('#pause').click()
    playwright.expect(page.locator('#pause')).to_have_text('Resume updates')
    page.locator('#refresh').click()
    playwright.expect(page.locator('#updated')).to_contain_text('Updated')
    page.route('**/api/youtube/download-cookies/status?refresh=1', lambda route: route.fulfill(json={'valid':True}))
    page.locator('#cookie-check').click()
    playwright.expect(page.locator('#cookie-result')).to_have_text('Audio test passed.')
    with page.expect_download() as download:
        page.locator('#export').click()
    path=tmp_path/'snapshot.json'; download.value.save_as(path)
    payload=json.loads(path.read_text())
    assert payload['scope']=='current_process'
    assert server.API_KEY not in path.read_text()
    page.locator('#search').fill('')
    page.locator('#pool').select_option('all')
    screenshot(page, 'musicbox-status-desktop.png', tmp_path)
    assert not errors


def test_mobile_no_overflow_secret_url_removed_and_failed_refresh_keeps_snapshot(browser_page, tmp_path):
    page, base, errors = browser_page
    page.set_viewport_size({'width':390,'height':844})
    page.goto(base+'/status?key='+server.API_KEY)
    playwright.expect(page.locator('#updated')).to_contain_text('Updated')
    assert 'key=' not in page.url
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    before=page.locator('#inventory-summary').inner_text()
    page.route('**/api/status', lambda route: route.fulfill(status=503,json={'error':'outage'}))
    page.locator('#refresh').click()
    playwright.expect(page.locator('#notice')).to_contain_text('Keeping the snapshot')
    assert before==page.locator('#inventory-summary').inner_text()
    screenshot(page, 'musicbox-status-mobile.png', tmp_path)
    assert not errors


def test_api_strings_are_text_and_expired_session_stops_polling(browser_page):
    page, base, errors = browser_page
    ready(page, base)
    payload=page.request.get(base+'/api/status').json()
    payload['alerts']=[{'level':'warning','title':'<img src=x onerror="window.injected=true">','detail':'<script>window.injected=true</script>'}]
    page.route('**/api/status', lambda route: route.fulfill(json=payload))
    page.locator('#refresh').click()
    playwright.expect(page.locator('#alerts')).to_contain_text('<img')
    assert page.evaluate('window.injected !== true')
    assert page.locator('#alerts img').count()==0
    page.unroute('**/api/status')
    page.route('**/api/status', lambda route: route.fulfill(status=401,json={'error':'expired'}))
    page.locator('#refresh').click()
    playwright.expect(page.locator('#notice')).to_contain_text('session expired')
    count=[]
    page.on('request',lambda request: count.append(request.url) if request.url.endswith('/api/status') else None)
    page.locator('#refresh').click()
    assert not count
    assert not errors

@pytest.mark.parametrize('width', [320, 390, 768, 1024])
def test_responsive_layout_capacity_and_expandable_details(browser_page, width):
    page, base, errors = browser_page
    page.set_viewport_size({'width':width,'height':900})
    ready(page, base)
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    playwright.expect(page.locator('#cache-used')).to_have_text('6 KiB')
    playwright.expect(page.locator('#cache-budget')).to_have_text('of 5 GiB used')
    assert page.locator('#cache-meter').get_attribute('aria-valuenow') is not None
    page.get_by_text('Capacity & cleanup policy', exact=True).click()
    playwright.expect(page.locator('#retention')).to_contain_text('Until space is needed · LRU')
    playwright.expect(page.locator('#retention')).to_contain_text('Shared with total music cache')
    page.get_by_text('Resource details', exact=True).click()
    playwright.expect(page.locator('#system')).to_be_visible()
    assert not errors


def test_capacity_meter_sampled_over_budget_unlimited_and_unavailable(browser_page, tmp_path):
    page, base, errors = browser_page
    ready(page, base)
    payload = page.request.get(base+'/api/status').json()
    mib = 1024*1024
    payload['storage']['audio'].update(ready_bytes=512*mib, warmup_bytes=128*mib, pinned_bytes=64*mib, partial_bytes=32*mib)
    payload['limits']['audio_bytes'] = 1024*mib
    def respond(route): route.fulfill(json=payload)
    page.route('**/api/status', respond)
    page.locator('#refresh').click()
    playwright.expect(page.locator('#cache-percent')).to_have_text('50%')
    playwright.expect(page.locator('#cache-used')).to_have_text('512 MiB')
    playwright.expect(page.locator('#cache-requested')).to_have_text('384 MiB')
    playwright.expect(page.locator('#cache-warmup')).to_have_text('128 MiB')
    assert page.locator('#cache-meter').get_attribute('aria-valuenow') == '50'
    assert page.locator('#cache-requested-bar').evaluate('el => el.style.width') == '37.5%'
    assert page.locator('#cache-warmup-bar').evaluate('el => el.style.width') == '12.5%'
    screenshot(page, 'musicbox-status-capacity-desktop.png', tmp_path)
    page.set_viewport_size({'width':390,'height':844})
    screenshot(page, 'musicbox-status-capacity-mobile.png', tmp_path)
    payload['storage']['audio']['complete'] = False
    page.locator('#refresh').click()
    playwright.expect(page.locator('#cache-used')).to_have_text('At least 512 MiB')
    playwright.expect(page.locator('#cache-remaining')).to_have_text('Not yet known')
    payload['storage']['audio'].update(complete=True,ready_bytes=2048*mib)
    page.locator('#refresh').click()
    playwright.expect(page.locator('#cache-percent')).to_have_text('200%')
    playwright.expect(page.locator('#cache-capacity-note')).to_contain_text('Over budget')
    assert page.locator('#cache-meter').get_attribute('aria-valuenow') == '100'
    payload['limits']['audio_bytes'] = 0
    page.locator('#refresh').click()
    playwright.expect(page.locator('#cache-budget')).to_have_text('used · no size limit')
    assert page.locator('#cache-meter').get_attribute('aria-valuenow') is None
    payload['storage']['audio']['available'] = False
    page.locator('#refresh').click()
    playwright.expect(page.locator('#cache-used')).to_have_text('—')
    playwright.expect(page.locator('#cache-capacity-note')).to_contain_text('unavailable')
    assert not errors
