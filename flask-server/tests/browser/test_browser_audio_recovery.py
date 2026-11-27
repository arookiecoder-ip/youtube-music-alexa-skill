"""Exercise retries, seeking and decoder cleanup with Chromium's real Audio element."""
import io
import wave
from pathlib import Path

from playwright.sync_api import sync_playwright

SOURCE = Path(__file__).resolve().parents[2] / 'templates/static/js/browser-player.js'


def test_network_failure_recovers_and_clear_releases_media():
    fixture = io.BytesIO()
    with wave.open(fixture, 'wb') as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(8000)
        stream.writeframes(b'\x10\x00\xf0\xff' * 80000)
    requests = []
    with sync_playwright() as driver:
        browser = driver.chromium.launch(args=['--autoplay-policy=no-user-gesture-required'])
        page = browser.new_page()
        page.route('https://musicbox.test/', lambda route: route.fulfill(body='<title>Player recovery</title>'))

        def audio(route):
            if 'prefetch=1' in route.request.url:
                route.fulfill(status=202, body='{}')
                return
            requests.append(route.request.url)
            if len(requests) == 1:
                route.abort('failed')
            else:
                route.fulfill(content_type='audio/wav', body=fixture.getvalue())

        page.route('https://musicbox.test/audio/**', audio)
        page.goto('https://musicbox.test/')
        page.evaluate('''() => {
            window.owner = '';
            window.api = async () => ({playback_output:'phone', output_owner:window.owner,
                output_token:'test-lease', phone_lease_ms:60000, handoff_pending:false, commands:[]});
            window.browserAudioUrl = id => '/audio/?video_id=' + id + '&wait=1';
        }''')
        page.add_script_tag(path=str(SOURCE))
        page.evaluate('''async () => {
            window.owner = browserPlayback.id;
            await browserPlayback.poll(); await browserPlayback.poll();
            await browserPlayback.execute('queue', {queue_items:[{
                video_id:'AAAAAAAAAAA',title:'Recovery fixture',duration_ms:20000
            }]});
        }''')
        page.wait_for_function('browserPlayback.audio.currentTime > 0.1 && !browserPlayback.audio.paused', timeout=15000)
        assert len(requests) == 2, 'A failed audio request should recover once, without a retry storm'
        page.evaluate('''() => {
            browserPlayback.audio.currentTime = 7;
            browserPlayback.audio.dispatchEvent(new Event('error'));
        }''')
        page.wait_for_function('browserPlayback.audio.currentTime >= 7 && !browserPlayback.audio.paused')
        page.wait_for_function('browserPlayback.audio.currentTime > 7.2 && browserPlayback.audio.readyState >= 3')
        # Wait for the scheduled source reopening, not only the old buffered audio.
        page.wait_for_function('performance.getEntriesByType("resource").filter(e => e.name.includes("/audio/")).length >= 3')
        page.wait_for_function('browserPlayback.audio.currentTime >= 7 && !browserPlayback.audio.paused && browserPlayback.audio.readyState >= 3')
        assert page.evaluate('browserPlayback.audio.currentTime') >= 7
        page.evaluate("browserPlayback.execute('clear', {})")
        page.wait_for_function('!browserPlayback.audio.src && browserPlayback.audio.readyState === 0')
        browser.close()
