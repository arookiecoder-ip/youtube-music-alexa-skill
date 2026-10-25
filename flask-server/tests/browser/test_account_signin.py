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
    import struct,zlib,base64
    def chunk(kind,data):return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data)&0xffffffff)
    png=b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',480,800,8,2,0,0,0))+chunk(b'IDAT',zlib.compress((b'\0'+b'\xff'*1440)*800))+chunk(b'IEND',b'')
    frame_image='data:image/png;base64,'+base64.b64encode(png).decode()
    @app.get('/accounts/<provider>')
    def account(provider):return render_template('account_signin.html',provider=provider)
    @app.get('/remote')
    def remote():return render_template('remote.html',asset_v='test')
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
            if 'session_expired' in (request.referrer or ''):return jsonify(error='unauthorized'),401
            if 'broken_frame' in (request.referrer or ''):return jsonify(image='data:image/png;base64,AA==',width=480,height=800)
            if 'frame_fail' in (request.referrer or '') and frame_errors[0]==0:
                frame_errors[0]+=1;return jsonify(error='Browser is starting'),503
            return jsonify(width=480,height=800,image=frame_image)
        if action=='capture':captured[0]=True
        return jsonify(state='connected' if captured[0] else 'waiting_for_login')
    server=make_server('127.0.0.1',0,app,threaded=True);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    with sync_playwright() as p:
        browser=p.chromium.launch();page=browser.new_page(viewport={'width':390,'height':844},has_touch=True,is_mobile=True)
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
    page.get_by_role('button',name='Use this account').click();expect(page).to_have_url(origin+'/?account_connected=youtube',timeout=5000)
    events=[body for action,body in calls if action=='surface' and body]
    assert any(x.get('type')=='click' for x in events)
    assert any(x=={'type':'key','key':'Enter'} for x in events)
    assert any(action=='capture' for action,_ in calls)

def test_cold_browser_start_is_loading_and_recovers_without_a_stale_error(account_page):
    page,origin,_=account_page;page.goto(origin+'/accounts/youtube?frame_fail=1')
    page.get_by_role('button',name='Continue to YouTube Music').click()
    expect(page.locator('#frame')).to_have_attribute('data-width','480')
    expect(page.locator('#status')).not_to_have_class('error')
    expect(page.locator('#status')).to_contain_text('Finish signing in')

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


def test_sidebar_device_list_fades_and_blocks_logged_out_echo(account_page):
    page,origin,_=account_page
    page.route('**/*',lambda route:route.abort() if route.request.resource_type=='script' else route.continue_())
    page.goto(origin+'/remote')
    page.evaluate("""() => { document.body.classList.remove('preload');window.api=async()=>({});window.escHtml=text=>text;window.toast=text=>window.message=text;document.getElementById('device').innerHTML='<option value="mobile:own">This browser</option><option value="echo" data-online="1" data-logged-out="1">Alexa (logged out)</option>'; }""")
    page.evaluate((ROOT/'templates/static/js/sidebar.js').read_text())
    page.locator('#hamburger-btn').evaluate('element=>element.click()')
    page.locator('#device-sidebar-trigger').evaluate('element=>element.click()')
    entry=page.locator('#device-sidebar-menu .custom-select-option[data-value="echo"]')
    expect(entry).to_have_class('custom-select-option logged-out')
    assert float(entry.evaluate('e=>getComputedStyle(e).opacity'))<.6
    entry.evaluate('element=>element.click()')
    assert page.locator('#device').input_value()=='mobile:own'
    assert 'Sign in to Amazon' in page.evaluate('window.message')


@pytest.mark.parametrize('failure',['session_expired','broken_frame'])
def test_failed_surface_never_reveals_broken_image_or_enables_controls(account_page,failure):
    page,origin,calls=account_page;page.goto(origin+'/accounts/youtube?'+failure+'=1')
    page.get_by_role('button',name='Continue to YouTube Music').click()
    expect(page.locator('#status')).to_have_class('error')
    expect(page.locator('#frame')).to_be_hidden()
    expect(page.locator('#browser-tools')).to_be_hidden()
    expect(page.locator('#capture')).to_be_disabled()
    assert page.locator('#frame').get_attribute('src') is None
    assert not [body for action,body in calls if action=='surface' and body]


def test_desktop_keyboard_works_without_mobile_toolbar(account_page):
    page,origin,calls=account_page
    desktop=page.context.browser.new_page(viewport={'width':1280,'height':900})
    try:
        desktop.goto(origin+'/accounts/youtube')
        desktop.get_by_role('button',name='Continue to YouTube Music').click()
        expect(desktop.locator('#frame')).to_have_attribute('data-width','480')
        expect(desktop.locator('.keyboard-tools')).to_be_hidden()
        desktop.locator('#frame').hover()
        desktop.mouse.wheel(0,350)
        desktop.wait_for_function('window.scrollY>30')
        desktop.locator('#frame').click(position={'x':40,'y':40})
        desktop.keyboard.type('account')
        desktop.keyboard.press('Tab')
        desktop.get_by_role('button',name='Use this account').click()
        expect(desktop).to_have_url(origin+'/?account_connected=youtube',timeout=5000)
        events=[body for action,body in calls if action=='surface' and body]
        assert ''.join(event.get('text','') for event in events)=='account'
        assert {'type':'key','key':'Tab'} in events
    finally:desktop.close()


def test_mobile_swipe_over_browser_can_reach_account_confirmation(account_page):
    page,origin,_=account_page;page.goto(origin+'/accounts/youtube')
    page.get_by_role('button',name='Continue to YouTube Music').click()
    expect(page.locator('#frame')).to_have_attribute('data-width','480')
    session=page.context.new_cdp_session(page)
    frame=page.locator('#frame').bounding_box();x=frame['x']+frame['width']/2;y=min(frame['y']+180,700)
    session.send('Input.dispatchTouchEvent',{'type':'touchStart','touchPoints':[{'x':x,'y':y}]})
    for step in range(1,11):
        session.send('Input.dispatchTouchEvent',{'type':'touchMove','touchPoints':[{'x':x,'y':y-step*25}]})
    session.send('Input.dispatchTouchEvent',{'type':'touchEnd','touchPoints':[]})
    page.wait_for_function('window.scrollY>30')
    expect(page.locator('#capture')).to_be_in_viewport()
