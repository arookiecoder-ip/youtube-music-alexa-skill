"""Real layout and browser audio checks without external accounts or devices."""
import io
from pathlib import Path
import wave
import pytest
from playwright.sync_api import sync_playwright, expect

STATIC = Path(__file__).resolve().parents[2] / 'templates' / 'static'


@pytest.mark.parametrize('width', [390, 1440])
def test_original_playlist_hero_before_pr43(width):
    with sync_playwright() as driver:
        browser = driver.chromium.launch()
        page = browser.new_page(viewport={'width': width, 'height': 844})
        page.set_content('<div id="collection-detail-shell"><div class="collection-detail-body"><div id="hero"></div><div style="height:2000px">Tracks</div></div></div>')
        page.add_style_tag(path=str(STATIC / 'css/playlist.css'))
        page.add_script_tag(path=str(STATIC / 'js/collection-renderer.js'))
        page.evaluate("""() => {
            document.querySelector('#hero').outerHTML = CollectionRenderer.renderDetailHero({
                id: 'hero', title: 'Original playlist', description: 'Original description', showActions: true
            });
            window.playClicks = 0;
            document.querySelector('.playlist-hero-play').onclick = () => playClicks++;
        }""")
        expect(page.locator('.playlist-detail-hero-desc')).to_be_visible()
        expect(page.locator('details, summary')).to_have_count(0)
        page.locator('.playlist-hero-play').click()
        assert page.evaluate('playClicks') == 1
        page.evaluate('window.scrollTo(0, 1000)')
        expect(page.locator('.collection-floating-play, .collection-scroll-header')).to_have_count(0)
        assert page.evaluate('CollectionRenderer.attachFloatingPlay === undefined')
        browser.close()


def test_pc_player_decodes_audio_and_stops_on_device_handoff():
    data = io.BytesIO()
    with wave.open(data, 'wb') as sound:
        sound.setnchannels(1); sound.setsampwidth(2); sound.setframerate(8000)
        sound.writeframes(b'\x00\x00' * 8000 * 30)
    with sync_playwright() as driver:
        browser = driver.chromium.launch()
        page = browser.new_page()
        def audio_response(route):
            payload = data.getvalue()
            headers = {'Access-Control-Allow-Origin': '*', 'Accept-Ranges': 'bytes'}
            requested = route.request.headers.get('range')
            if requested:
                bounds = requested.removeprefix('bytes=').split('-')
                start, end = int(bounds[0] or 0), int(bounds[1] or len(payload) - 1)
                end = min(end, len(payload) - 1)
                headers['Content-Range'] = f'bytes {start}-{end}/{len(payload)}'
                route.fulfill(status=206, body=payload[start:end + 1], content_type='audio/wav', headers=headers)
            else:
                route.fulfill(body=payload, content_type='audio/wav', headers=headers)
        page.route('https://audio.example/**', audio_response)
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
        page.evaluate("window.api('/alexa/seek/', {position_ms: 12000})")
        page.wait_for_function('browserPlayback.audio.currentTime >= 12 && browserPlayback.audio.currentTime < 15')
        page.evaluate("window.api('/alexa/command/', {action: 'pause'})")
        page.evaluate("window.api('/alexa/seek/', {position_ms: 4000})")
        page.wait_for_function('browserPlayback.audio.currentTime >= 4 && browserPlayback.audio.currentTime < 4.5')
        assert page.evaluate('browserPlayback.audio.paused')
        page.evaluate("window.api('/alexa/command/', {action: 'play'})")
        page.wait_for_function('browserPlayback.audio.currentTime > 4.1 && !browserPlayback.audio.paused')
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


@pytest.mark.parametrize('mode,expected', [
    ('offline', 'mobile:browser-current'),
    ('online', 'echo'),
    ('phone', 'mobile:other-phone'),
    ('unavailable-phone', 'mobile:browser-current'),
])
def test_startup_device_selection_survives_init_and_refresh(mode, expected):
    with sync_playwright() as driver:
        browser = driver.chromium.launch()
        page = browser.new_page()
        page.route('https://musicbox.test/', lambda route: route.fulfill(
            body='<select id="device"></select>', content_type='text/html'))
        page.goto('https://musicbox.test/')
        page.evaluate("""mode => {
            window.__appState = {}; window.errors = [];
            window.toast = text => errors.push(text); window.escHtml = text => text;
            window.browserPlayback = {id: 'browser-current'};
            localStorage.setItem('selectedSerial', 'mobile:browser-expired');
            window.echoes = [{serial: 'echo', name: 'Echo', online: mode === 'online'}];
            window.output = {playback_output: mode.includes('phone') ? 'phone' : 'alexa',
                output_owner: mode.includes('phone') ? 'other-phone' : '', output_serial: 'echo',
                output_token: 'token', output_epoch: 'epoch', output_revision: 1,
                devices: mode === 'phone' ? [{id:'other-phone', name:'Phone'}] : []};
            window.api = async path => path.startsWith('/alexa/init/')
                ? {status:{logged_in:true, configured:true}, devices:echoes, serial:'echo'} : output;
        }""", mode)
        page.add_script_tag(path=str(STATIC / 'js/device.js'))
        page.add_script_tag(path=str(STATIC / 'js/device-connect.js'))
        page.evaluate('window.initPage()')
        expect(page.locator('#device')).to_have_value(expected)
        # Late cache / Alexa inventory refresh must go through the same resolver.
        page.evaluate("window._applyDevices(echoes, 'echo')")
        expect(page.locator('#device')).to_have_value(expected)
        assert page.locator('#device option[value="mobile:browser-current"]').count() == 1
        if mode in ('offline', 'unavailable-phone'):
            assert not any('offline' in error for error in page.evaluate('errors'))
        browser.close()
