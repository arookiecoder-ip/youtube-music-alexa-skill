"""Real layout and browser audio checks without external accounts or devices."""
import io
from pathlib import Path
import wave
import pytest
from playwright.sync_api import sync_playwright, expect

STATIC = Path(__file__).resolve().parents[2] / 'templates' / 'static'


@pytest.mark.parametrize('width', [390, 1440])
def test_description_and_floating_play(width):
    with sync_playwright() as driver:
        browser = driver.chromium.launch()
        page = browser.new_page(viewport={'width': width, 'height': 844})
        page.set_content('<div class="collection-detail-body" style="height:320px;overflow:auto"><div id="hero"></div><div style="height:2000px">Tracks</div></div>')
        page.add_style_tag(path=str(STATIC / 'css/playlist.css'))
        page.add_script_tag(path=str(STATIC / 'js/collection-renderer.js'))
        page.evaluate('''() => {
            document.querySelector('#hero').outerHTML = CollectionRenderer.renderDetailHero({
                id: 'hero', title: 'Long playlist', description: 'Unbroken'.repeat(1000), showActions: true
            });
            window.playClicks = 0;
            const hero = document.querySelector('#hero');
            hero.querySelector('.playlist-hero-play').onclick = () => playClicks++;
            CollectionRenderer.attachFloatingPlay(hero);
        }''')
        expect(page.locator('.playlist-detail-hero-desc')).not_to_be_visible()
        page.locator('summary').click()
        expect(page.locator('.playlist-detail-hero-desc')).to_be_visible()
        assert page.locator('.collection-detail-body').evaluate('(el) => el.scrollWidth <= el.clientWidth')
        page.locator('summary').click()
        page.locator('.collection-detail-body').evaluate('(el) => { el.scrollTop = 500; }')
        expect(page.locator('.collection-floating-play')).to_be_visible()
        page.locator('.collection-floating-play').click()
        assert page.evaluate('playClicks') == 1
        page.evaluate("window.dispatchEvent(new Event('hashchange'))")
        expect(page.locator('.collection-floating-play')).to_have_count(0)
        browser.close()


def test_pc_player_decodes_audio_and_stops_on_device_handoff():
    data = io.BytesIO()
    with wave.open(data, 'wb') as sound:
        sound.setnchannels(1); sound.setsampwidth(2); sound.setframerate(8000)
        sound.writeframes(b'\x00\x00' * 8000 * 5)
    with sync_playwright() as driver:
        browser = driver.chromium.launch()
        page = browser.new_page()
        page.route('https://audio.example/**', lambda route: route.fulfill(
            body=data.getvalue(), content_type='audio/wav', headers={'Access-Control-Allow-Origin': '*'}))
        page.route('https://musicbox.test/', lambda route: route.fulfill(
            body='<select id="device"></select><button id="play">Play</button>', content_type='text/html'))
        page.goto('https://musicbox.test/')
        page.evaluate('''() => {
            window.output = null; window.sent = []; window.errors = [];
            window.toast = text => errors.push(text);
            window.browserAudioUrl = () => 'https://audio.example/track.wav';
            window.api = async (path, body) => {
                if (path === '/api/app/devices/') {
                    if (!output) output = { playback_output: 'phone', output_owner: body.device_id,
                        output_token: 'lease', phone_lease_ms: 12000, handoff_pending: false };
                    return {...output, commands: [], now_playing: {queue: []}};
                }
                if (path === '/api/app/output/') return output;
                sent.push(body); return {ok: true};
            };
        }''')
        page.add_script_tag(path=str(STATIC / 'js/browser-player.js'))
        page.wait_for_function('window.browserPlayback && browserPlayback.owns()')
        page.evaluate('''() => {
            document.querySelector('#device').add(new Option('This browser', 'mobile:' + browserPlayback.id));
            document.querySelector('#play').onclick = () => window.api('/alexa/play_queue/', {
                queue_items: [{video_id: 'AAAAAAAAAAA', title: 'Local test audio'}]
            }).catch(error => errors.push(error.message));
        }''')
        page.locator('#play').click()
        page.wait_for_function('browserPlayback.audio.currentTime > 0.1 && !browserPlayback.audio.paused')
        assert page.evaluate("sent.some(item => item.action === 'start' && item.output_owner === browserPlayback.id)")
        assert page.evaluate('errors') == []
        page.evaluate("output = {...output, output_owner: 'another-device', output_token: 'new-lease'}")
        page.evaluate('browserPlayback.poll()')
        assert page.evaluate('browserPlayback.audio.paused && !browserPlayback.owns()')
        browser.close()


def test_browser_controls_do_not_require_amazon_or_public_echo_url():
    with sync_playwright() as driver:
        browser = driver.chromium.launch()
        page = browser.new_page()
        page.route('https://musicbox.test/', lambda route: route.fulfill(
            body='<select id="device"></select><section id="login-section"></section><button class="needs-login" hidden>Search music</button>',
            content_type='text/html'))
        page.goto('https://musicbox.test/')
        page.evaluate('''() => {
            window.__appState = {}; window.browserPlayback = {}; window.errors = [];
            window.toast = text => errors.push(text);
            window.escHtml = text => text;
            window.api = async () => ({status: {configured: false, logged_in: false}, devices: []});
        }''')
        page.add_script_tag(path=str(STATIC / 'js/device.js'))
        page.evaluate('window.initPage()')
        expect(page.get_by_text('Search music')).to_be_visible()
        expect(page.locator('#login-section')).not_to_be_visible()
        assert page.evaluate('__appState._loggedIn && !__appState._alexaLoggedIn')
        assert page.evaluate('errors') == []
        browser.close()
