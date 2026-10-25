"""Responsive account pages and signed-out Alexa controls in real Chromium."""
from pathlib import Path
import threading
import pytest
from flask import Flask,render_template,request,jsonify
from werkzeug.serving import make_server
from playwright.sync_api import sync_playwright,expect
ROOT=Path(__file__).resolve().parents[2]

@pytest.fixture
def account_page():
    app=Flask(__name__,template_folder=str(ROOT/'templates'),static_folder=str(ROOT/'templates/static'))
    calls=[];captured=[False];frame_errors=[0]
    @app.get('/accounts/<provider>')
    def account(provider):return render_template('account_signin.html',provider=provider)
    @app.get('/')
    def done():return 'Connected'
    @app.route('/alexa/proxy/<path:path>')
    def amazon(path):return '<meta name="viewport" content="width=device-width"><label>Email<input></label>'
    @app.route('/alexa/proxy_login/',methods=['POST'])
    def proxy():calls.append((request.path,request.json));return jsonify(login_url='/alexa/proxy/signin',attempt_id='attempt-one')
    @app.post('/alexa/proxy_cancel/')
    def cancel_amazon():calls.append((request.path,request.json));return jsonify(cancelled=True)
    @app.get('/alexa/proxy_check/')
    def check():return jsonify(logged_in=False)
    @app.route('/api/youtube/browser-session/<action>',methods=['GET','POST'])
    def youtube(action):
        calls.append((action,request.get_json(silent=True)))
        if action=='surface' and request.method=='GET':
            if 'frame_fail' in (request.referrer or '') and frame_errors[0]==0:
                frame_errors[0]+=1;return jsonify(error='Browser is starting'),503
            import base64
            svg=b'<svg xmlns="http://www.w3.org/2000/svg" width="480" height="800"><rect width="480" height="800" fill="white"/></svg>'
            return jsonify(width=480,height=800,image='data:image/svg+xml;base64,'+base64.b64encode(svg).decode())
        if action=='capture':captured[0]=True
        return jsonify(state='connected' if captured[0] else 'waiting_for_login')
    server=make_server('127.0.0.1',0,app,threaded=True);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    with sync_playwright() as p:
        browser=p.chromium.launch();page=browser.new_page(viewport={'width':390,'height':844})
        yield page,f'http://127.0.0.1:{server.server_port}',calls
        browser.close()
    server.shutdown();thread.join()

@pytest.mark.parametrize('width',[390,1280])
def test_amazon_dedicated_page_and_verification_stays_responsive(account_page,width):
    page,origin,calls=account_page;page.set_viewport_size({'width':width,'height':844});page.goto(origin+'/accounts/amazon')
    expect(page.get_by_role('heading',name='Amazon',exact=True)).to_be_visible()
    page.get_by_label('Amazon email').fill('owner@example.test');page.get_by_label('Password',exact=True).fill('test-password')
    page.get_by_role('button',name='Continue to Amazon').click()
    expect(page.locator('#amazon-page')).to_be_visible();expect(page.locator('#password')).to_have_value('')
    assert calls[0][1]=={'email':'owner@example.test','password':'test-password','force':True}
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    assert page.locator('iframe[src*="vnc"]').count()==0
    page.get_by_role('button',name='Cancel sign-in').click();expect(page).to_have_url(origin+'/')
    assert ('/alexa/proxy_cancel/',{'attempt_id':'attempt-one'}) in calls

def test_youtube_touch_keyboard_capture_and_cancel(account_page):
    page,origin,calls=account_page;page.goto(origin+'/accounts/youtube')
    assert not calls
    page.get_by_role('button',name='Continue to YouTube Music').click();expect(page.locator('#frame')).to_have_attribute('data-width','480')
    page.locator('#frame').click(position={'x':40,'y':50});page.get_by_role('button',name='Next field').click();page.get_by_role('button',name='Enter',exact=True).click()
    page.wait_for_function('document.querySelector("#frame").dataset.height==="800"')
    page.get_by_role('button',name='Use this YouTube Music account').click();expect(page).to_have_url(origin+'/?account_connected=youtube',timeout=5000)
    events=[body for action,body in calls if action=='surface' and body]
    assert any(x.get('type')=='click' for x in events)
    assert any(x=={'type':'key','key':'Enter'} for x in events)
    assert any(action=='capture' for action,_ in calls)

def test_cold_browser_start_is_loading_and_recovers_without_a_stale_error(account_page):
    page,origin,_=account_page;page.goto(origin+'/accounts/youtube?frame_fail=1')
    page.get_by_role('button',name='Continue to YouTube Music').click()
    expect(page.locator('#frame')).to_have_attribute('data-width','480')
    expect(page.locator('#status')).not_to_have_class('error')
    expect(page.locator('#status')).to_contain_text('Complete sign-in')

def test_mobile_ime_composition_is_sent_once_when_committed(account_page):
    page,origin,calls=account_page;page.goto(origin+'/accounts/youtube')
    page.get_by_role('button',name='Continue to YouTube Music').click()
    expect(page.locator('#frame')).to_have_attribute('data-width','480')
    page.evaluate("""() => { const field=document.getElementById('remote-keyboard');field.value='বাংলা';field.dispatchEvent(new InputEvent('input',{data:'বাংলা',isComposing:true,inputType:'insertCompositionText'}));field.dispatchEvent(new CompositionEvent('compositionend',{data:'বাংলা'}));field.dispatchEvent(new InputEvent('input',{data:'বাংলা',isComposing:false,inputType:'insertText'})); }""")
    page.get_by_role('button',name='Enter',exact=True).click()
    page.wait_for_timeout(200)
    assert [body['text'] for action,body in calls if action=='surface' and body and body.get('type')=='text']==['বাংলা']

def test_readonly_controls_explain_block_and_restore_after_handoff(account_page):
    page,_,_=account_page
    page.set_content('<button id="pp-btn">Play</button><button data-action="next">Next</button><div class="progress-track" tabindex="0"></div><div class="queue-item">Song</div><button id="device">Devices</button>')
    page.evaluate('() => { window.toast=(text)=>window.message=text;window.clicks=0;document.querySelector("#pp-btn").onclick=()=>window.clicks++; }')
    page.evaluate((ROOT/'templates/static/js/alexa-readonly.js').read_text())
    page.evaluate("updateAlexaControlState({playback_output:'alexa',remote_control_available:false})")
    page.locator('#pp-btn').click(force=True);assert page.evaluate('window.clicks')==0
    assert 'Sign in to Amazon' in page.evaluate('window.message')
    expect(page.locator('#pp-btn')).to_have_attribute('aria-disabled','true')
    page.evaluate("updateAlexaControlState({playback_output:'phone',remote_control_available:true})")
    page.locator('#pp-btn').click();assert page.evaluate('window.clicks')==1
    expect(page.locator('body')).not_to_have_class('alexa-controls-unavailable')

def test_profile_outline_updates_without_opening_menu(account_page):
    page,_,_=account_page;page.set_content('<button id="profile-menu-trigger" class="profile-menu-trigger">Profile</button>')
    page.add_style_tag(content=(ROOT/'templates/static/css/sidebar.css').read_text())
    page.evaluate('window.api=async()=>({logged_in:false})')
    code=(ROOT/'templates/static/js/sidebar.js').read_text().split('// Connection health is independent')[1]
    page.evaluate('// Connection health is independent'+code)
    expect(page.locator('#profile-menu-trigger')).to_have_class('profile-menu-trigger amazon-disconnected')
    assert page.locator('#profile-menu-trigger').evaluate('e=>getComputedStyle(e).borderTopColor')=='rgb(241, 111, 101)'
    page.evaluate('setAmazonConnection(true)');expect(page.locator('#profile-menu-trigger')).not_to_have_class('profile-menu-trigger amazon-disconnected')
