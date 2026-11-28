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


def test_active_browser_audio_confirms_leave_and_pause_removes_confirmation():
    fixture=io.BytesIO()
    with wave.open(fixture,'wb') as sound:
        sound.setnchannels(1);sound.setsampwidth(2);sound.setframerate(8000);sound.writeframes(b'\0\0'*160000)
    with sync_playwright() as driver:
        browser=driver.chromium.launch()
        page=browser.new_page(); dialogs=[]
        page.route('https://musicbox.test/',lambda route:route.fulfill(body='<button id="play">Play</button><a id="leave" href="/next">Leave</a>'))
        page.route('https://musicbox.test/next',lambda route:route.fulfill(body='Next page'))
        page.route('https://musicbox.test/audio/**',lambda route:route.fulfill(content_type='audio/wav',body=fixture.getvalue()))
        page.goto('https://musicbox.test/')
        page.evaluate('''() => {
            window.owner='';
            window.api=async()=>({playback_output:'phone',output_owner:owner,output_token:'lease',phone_lease_ms:60000,handoff_pending:false,commands:[]});
            window.browserAudioUrl=id=>'/audio/?video_id='+id;
        }''')
        page.add_script_tag(path=str(SOURCE))
        page.evaluate('''() => { document.getElementById('play').onclick=async()=>{
            window.owner=browserPlayback.id;await browserPlayback.poll();
            await browserPlayback.execute('queue',{queue_items:[{video_id:'AAAAAAAAAAA',title:'Fixture'}]});
        }; }''')
        page.locator('#play').click()
        page.wait_for_function('!browserPlayback.audio.paused && browserPlayback.audio.currentTime>0.05')
        def dismiss(dialog):
            dialogs.append(dialog.type);dialog.dismiss()
        page.on('dialog',dismiss)
        page.locator('#leave').click()
        assert dialogs==['beforeunload']
        assert page.url=='https://musicbox.test/'
        assert not page.evaluate('browserPlayback.audio.paused')
        page.evaluate("browserPlayback.execute('pause',{})")
        page.locator('#leave').click()
        page.wait_for_url('https://musicbox.test/next')
        assert dialogs==['beforeunload'], 'Paused playback must allow navigation without another confirmation'
        browser.close()
