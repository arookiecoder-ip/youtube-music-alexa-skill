"""Exercise the app-style browser shell against isolated backend fixtures."""
import io
import os
import threading
import wave
from pathlib import Path

import pytest
from flask import Flask, jsonify, render_template, request, Response
from werkzeug.serving import make_server
from playwright.sync_api import sync_playwright, expect

ROOT=Path(__file__).resolve().parents[2]


@pytest.fixture(scope='module')
def app_origin():
    app=Flask(__name__,template_folder=str(ROOT/'templates'),static_folder=str(ROOT/'templates/static'))
    state={'timer':0,'requests':[]}
    @app.route('/alexa/<path:path>',methods=['GET','POST'])
    def alexa(path):
        if path.rstrip('/')=='init': return jsonify(logged_in=True, authenticated=True, devices=[{'serialNumber':'echo','accountName':'Living room'}],serial='echo')
        if path.rstrip('/')=='status': return jsonify(logged_in=True)
        if path.rstrip('/')=='now_playing': return jsonify(video_id='AAAAAAAAAAA',title='Night drive',artist='The test band',playing=False,queue=[],position_ms=2000,duration_ms=200000,playback_confirmed=True)
        return jsonify(ok=True,devices=[])
    @app.route('/api/<path:path>',methods=['GET','POST','DELETE'])
    def api(path):
        state['requests'].append((path,request.get_json(silent=True)))
        if path.rstrip('/')=='app/sleep-timer':
            if request.method=='POST': state['timer']=(request.get_json() or {}).get('minutes',0)
            return jsonify(remaining_ms=state['timer']*60000,playback_output='alexa',output_owner='')
        if path.endswith('/lyrics'): return jsonify(available=True,text='First line\nNext line',lines=[{'text':'First line','start_ms':0},{'text':'Next line','start_ms':1000}])
        if path.rstrip('/')=='app/devices': return jsonify(playback_output='alexa',output_serial='echo',output_token='epoch',devices=[])
        if path.rstrip('/')=='home': return jsonify(schemaVersion=2,shelves=[])
        if path.rstrip('/')=='library': return jsonify(playlists=[{'playlistId':'PLfixture','title':'My playlist','count':4}],albums=[{'browseId':'MPREfixture','title':'Saved album'}])
        if path.rstrip('/')=='liked_songs': return jsonify(liked_songs=[])
        if path.rstrip('/')=='profile_status': return jsonify(authenticated=True,alexa_logged_in=True)
        return jsonify(ok=True,artists=[],tracks=[],songs=[],results=[],playlists=[])
    @app.get('/audio/')
    def audio():
        if request.args.get('audio_tracks'):return jsonify(audio_tracks=[{'id':'en','display_name':'English'}],selected='en')
        data=io.BytesIO()
        with wave.open(data,'wb') as sound:
            sound.setnchannels(1);sound.setsampwidth(2);sound.setframerate(8000);sound.writeframes(b'\0\0'*8000)
        return Response(data.getvalue(),mimetype='audio/wav')
    @app.get('/<path:path>')
    @app.get('/')
    def page(path=''):
        if path.endswith('stream') or path.endswith('events'):return Response('',mimetype='text/event-stream')
        return render_template('remote.html',asset_v='parity',remote_username='Listener',is_authenticated=True,account_available=True,alexa_access_enabled=True)
    server=make_server('127.0.0.1',0,app,threaded=True)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    yield f'http://127.0.0.1:{server.server_port}',state
    server.shutdown();thread.join()


@pytest.mark.parametrize('width',[390,820,1440])
def test_app_navigation_settings_timer_lyrics_and_download(app_origin,width,tmp_path):
    origin,state=app_origin
    with sync_playwright() as driver:
        browser=driver.chromium.launch()
        context=browser.new_context(viewport={'width':width,'height':900},accept_downloads=True)
        page=context.new_page();errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
        def screenshot(name):
            directory=Path(os.environ.get('MUSICBOX_PARITY_SCREENSHOTS',str(tmp_path)))
            directory.mkdir(parents=True,exist_ok=True)
            page.screenshot(path=str(directory/f'{name}-{width}.png'),full_page=name in ('search','library','settings'),animations='disabled')
        page.goto(origin+'/home')
        page.wait_for_function("!!window.browserPreferences && typeof window.navigateTo === 'function'")
        page.evaluate("document.body.classList.remove('preload')")
        if width<900:
            expect(page.get_by_role('navigation',name='Main navigation')).to_be_visible()
            page.locator('.app-dock [data-app-route="#search"]').click()
        else:page.evaluate("navigateTo('#search')")
        expect(page.get_by_role('heading',name='Search',exact=True)).to_be_visible()
        expect(page.get_by_role('link',name='New releases & charts',exact=False)).to_be_visible()
        screenshot('search')
        page.evaluate("navigateTo('#library')")
        expect(page.locator('.library-card-album')).to_be_visible()
        screenshot('library')
        page.locator('[data-library-filter="albums"]').click()
        expect(page.locator('.library-card:not(.library-card-album)')).not_to_be_visible()
        page.get_by_role('searchbox',name='Search your library').fill('Saved')
        expect(page.locator('.library-card-album')).to_be_visible()
        page.get_by_role('button',name='Toggle library layout').click()
        assert not page.locator('#library-page').evaluate("el=>el.classList.contains('app-library-list')")
        page.locator('.library-card-album').click()
        expect(page).to_have_url(origin+'/album?browse=MPREfixture')
        page.evaluate("navigateTo('#settings')")
        expect(page.get_by_role('heading',name='Settings',exact=True)).to_be_visible()
        page.locator('[data-pref="preload"]').uncheck()
        assert page.evaluate('browserPreferences.preload') is False
        page.reload();page.wait_for_function('window.browserPreferences')
        expect(page.locator('[data-pref="preload"]')).not_to_be_checked()
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        if width>=900:
            page.wait_for_function("document.querySelector('#browser-settings-page').getBoundingClientRect().left >= document.querySelector('.sidebar').getBoundingClientRect().right")
        expect(page.locator('[data-pref="theme"]')).to_have_value('dark')
        assert page.locator('[data-pref="theme"]').bounding_box()['width']>=100
        screenshot('settings')
        if width>=900:
            assert page.get_by_role('heading',name='Settings',exact=True).bounding_box()['x']>=page.locator('.sidebar').bounding_box()['width']
        page.evaluate("""() => {
            __appState._currentTrack={video_id:'AAAAAAAAAAA',title:'Night drive',artist:'The test band'};
            __appState._currentVideoId='AAAAAAAAAAA';__appState._hasTrack=true;
            dispatchEvent(new CustomEvent('musicbox:track'));
            openNowPlayingOverlay();
        }""")
        tools=page.locator('#app-player-tools')
        if width>=900: expect(tools).to_be_visible()
        def player_action(name):
            if width<900:
                page.locator('#mobile-player-more').click()
                page.locator('#np-more-menu').get_by_role('menuitem',name=name,exact=True).click()
            else: tools.get_by_role('button',name=name,exact=name!='Sleep timer').click()
        screenshot('player')
        player_action('Sleep timer')
        expect(page.get_by_role('dialog',name='Sleep timer')).to_be_visible()
        screenshot('sleep-timer')
        page.get_by_label('Custom timer').fill('17')
        page.get_by_role('button',name='Set timer',exact=True).click()
        expect(page.get_by_role('dialog',name='Sleep timer')).not_to_be_visible()
        assert state['timer']==17
        player_action('Lyrics')
        expect(page.locator('#app-lyrics-body')).to_contain_text('Next line')
        screenshot('lyrics')
        page.get_by_role('button',name='Close lyrics',exact=True).click()
        with page.expect_download() as download:
            player_action('Download')
        assert download.value.suggested_filename=='Night drive.wav'
        page.evaluate("closeNowPlayingOverlay ? closeNowPlayingOverlay() : navigateTo('#home')")
        page.evaluate("navigateTo('#downloads')")
        expect(page.locator('#app-download-list')).to_contain_text('Night drive')
        assert not errors,errors
        browser.close()


def test_browser_audio_effects_apply_and_reset_without_compressing_normal_audio(app_origin):
    origin,_=app_origin
    with sync_playwright() as driver:
        browser=driver.chromium.launch()
        page=browser.new_page()
        page.add_init_script('''(() => {
            const NativeContext=window.AudioContext;
            window.AudioContext=class extends NativeContext {
                createGain(){const node=super.createGain();window.effectGain=node;return node;}
                createBiquadFilter(){const node=super.createBiquadFilter();window.effectBass=node;return node;}
                createDynamicsCompressor(){const node=super.createDynamicsCompressor();window.effectLimiter=node;return node;}
            };
        })()''')
        page.goto(origin+'/settings')
        page.wait_for_function('!!window.browserPreferences')
        page.evaluate('browserPlayback.owns=()=>true')
        page.get_by_role('slider',name='Volume boost').focus()
        page.get_by_role('slider',name='Volume boost').press('ArrowRight')
        page.wait_for_function('!!window.effectGain')
        assert page.evaluate('effectGain.gain.value')>1
        page.get_by_role('slider',name='Bass boost').focus()
        page.get_by_role('slider',name='Bass boost').press('ArrowRight')
        assert page.evaluate('effectBass.gain.value')>0
        page.get_by_role('button',name='Reset audio effects').click()
        assert page.evaluate('effectGain.gain.value')==1
        assert page.evaluate('effectBass.gain.value')==0
        assert page.evaluate('effectLimiter.threshold.value')==0
        browser.close()
