"""Exercise background synchronization with the real API helper and DOM."""
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

STATIC = Path(__file__).resolve().parents[2] / 'templates' / 'static'


def test_background_playback_requests_never_hold_foreground_loading():
    with sync_playwright() as driver:
        browser = driver.chromium.launch()
        page = browser.new_page()
        page.route('https://musicbox.test/', lambda route: route.fulfill(
            body='<div id="top-progress-bar"></div>', content_type='text/html'))
        page.goto('https://musicbox.test/')
        page.evaluate('''() => {
            window.pending = {};
            window.fetch = url => new Promise(resolve => pending[url] = () => resolve({ok:true, json:async()=>({ok:true})}));
        }''')
        page.add_script_tag(path=str(STATIC / 'js/api.js'))
        page.evaluate('''() => {
            window.requests = ['/api/app/devices/', '/api/app/output/', '/api/app/queue/'].map(url => api(url, {}));
        }''')
        page.wait_for_timeout(80)
        expect(page.locator('#top-progress-bar')).not_to_have_class('loading')
        page.evaluate("() => { window.search = api('/alexa/search/?q=test'); }")
        expect(page.locator('#top-progress-bar')).to_have_class('loading')
        page.evaluate("async () => { pending['/alexa/search/?q=test'](); await search; }")
        expect(page.locator('#top-progress-bar')).not_to_have_class('loading')
        page.evaluate('''async () => {
            for (const url of ['/api/app/devices/', '/api/app/output/', '/api/app/queue/']) pending[url]();
            await Promise.all(requests);
        }''')
        browser.close()


def test_obsolete_status_request_can_be_cancelled():
    with sync_playwright() as driver:
        browser = driver.chromium.launch()
        page = browser.new_page()
        page.route('https://musicbox.test/', lambda route: route.fulfill(body='<div></div>', content_type='text/html'))
        page.goto('https://musicbox.test/')
        page.evaluate('''() => {
            window.fetch = (url, options) => new Promise((resolve, reject) => {
                options.signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')));
            });
        }''')
        page.add_script_tag(path=str(STATIC / 'js/api.js'))
        result = page.evaluate('''async () => {
            const controller = new AbortController();
            const request = api('/alexa/now_playing/?serial=old', undefined, {signal:controller.signal}).then(() => false, () => true);
            controller.abort();
            return request;
        }''')
        assert result
        browser.close()


def test_status_cancellation_still_works_after_headers_arrive():
    with sync_playwright() as driver:
        browser = driver.chromium.launch()
        page = browser.new_page()
        page.route('https://musicbox.test/', lambda route: route.fulfill(body='<div></div>', content_type='text/html'))
        page.goto('https://musicbox.test/')
        page.evaluate("""() => {
            window.fetch = async (url, options) => ({ok:true, json:() => new Promise((resolve,reject) => {
                window.bodyStarted = true;
                options.signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')));
            })});
        }""")
        page.add_script_tag(path=str(STATIC / 'js/api.js'))
        page.evaluate("""() => {
            window.controller = new AbortController();
            window.request = api('/alexa/now_playing/?serial=old', undefined, {signal:controller.signal})
                .then(() => false, () => true);
        }""")
        page.wait_for_function('window.bodyStarted')
        assert page.evaluate('async () => { controller.abort(); return await request; }')
        browser.close()


def test_switching_selected_device_cancels_the_old_poll():
    with sync_playwright() as driver:
        browser = driver.chromium.launch()
        page = browser.new_page()
        page.route('https://musicbox.test/', lambda route: route.fulfill(
            body='<select id="device"><option value="first">First</option><option value="second">Second</option></select>',
            content_type='text/html'))
        page.goto('https://musicbox.test/')
        page.evaluate('''() => {
            window.__appState = {lastActionAt:0, lastActionIntent:null, GRACE_MS:0};
            window.rendered = [];
            window.showNowPlaying = np => rendered.push(np && np.title);
            window.fetch = (url, options) => {
                if (url.includes('serial=first')) return new Promise((resolve,reject) => {
                    window.firstStarted = true;
                    options.signal.addEventListener('abort', () => {
                        window.firstAborted = true;
                        reject(new DOMException('Aborted', 'AbortError'));
                    });
                });
                return Promise.resolve({ok:true, json:async()=>({title:'Second device', video_id:'BBBBBBBBBBB',
                    playing:false, playback_confirmed:true})});
            };
        }''')
        page.add_script_tag(path=str(STATIC / 'js/api.js'))
        page.add_script_tag(path=str(STATIC / 'js/sse.js'))
        page.evaluate('window.connectSSE()')
        page.wait_for_function('window.firstStarted')
        page.select_option('#device', 'second')
        page.wait_for_function('window.firstAborted && rendered.includes("Second device")', timeout=2000)
        assert page.evaluate('rendered.every(title => title === "Second device")')
        browser.close()
