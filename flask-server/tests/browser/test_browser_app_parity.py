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
def test_features_preserve_browser_layout(app_origin,width):
    origin,state=app_origin
    with sync_playwright() as driver:
        browser=driver.chromium.launch()
        page=browser.new_page(viewport={'width':width,'height':900},accept_downloads=True)
        errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
        page.goto(origin+'/home')
        page.wait_for_function("typeof window.navigateTo === 'function'")
        assert page.locator('.app-dock, #browser-settings-page, [data-pref]').count()==0
        page.evaluate("""() => {
            __appState._currentTrack={video_id:'AAAAAAAAAAA',title:'Night drive',artist:'The test band'};
            __appState._currentVideoId='AAAAAAAAAAA';__appState._hasTrack=true;
            dispatchEvent(new CustomEvent('musicbox:track'));
            openNowPlayingOverlay();
        }""")
        def action(name):
            page.locator('#np-more-menu').get_by_role('menuitem',name=name,exact=True,include_hidden=True).evaluate('el=>el.click()')
        action('Sleep timer')
        expect(page.get_by_role('dialog',name='Sleep timer')).to_be_visible()
        page.get_by_label('Custom timer').fill('17')
        page.get_by_role('button',name='Set timer',exact=True).click()
        expect(page.get_by_role('dialog',name='Sleep timer')).not_to_be_visible()
        assert state['timer']==17
        action('Lyrics')
        expect(page.locator('#app-lyrics-body')).to_contain_text('Next line')
        page.get_by_role('button',name='Close lyrics',exact=True).click()
        with page.expect_download() as download: action('Download')
        assert download.value.suggested_filename=='Night drive.wav'
        assert not errors,errors
        browser.close()
