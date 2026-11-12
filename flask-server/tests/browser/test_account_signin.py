"""Responsive account pages and signed-out Alexa controls in real Chromium.

The provider sign-in pages are a form stepper now: every answer the user types
is submitted to Music Box's own endpoints and a scripted browser does the
signing in. These tests drive the stepper against a scripted backend, the way
the old suite drove the removed screenshot surface.
"""
from pathlib import Path
import threading
import pytest
from flask import Flask,render_template,request,jsonify,Response
from werkzeug.serving import make_server
from playwright.sync_api import sync_playwright,expect
ROOT=Path(__file__).resolve().parents[2]

def _png_bytes():
    import struct,zlib
    def chunk(kind,data):return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data)&0xffffffff)
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',308,90,8,2,0,0,0))+chunk(b'IDAT',zlib.compress((b'\0'+b'\xff'*924)*90))+chunk(b'IEND',b'')

@pytest.fixture
def account_page():
    app=Flask(__name__,template_folder=str(ROOT/'templates'),static_folder=str(ROOT/'templates/static'))
    calls=[];queue=[]
    def record():
        query='?'+request.query_string.decode() if request.query_string else ''
        calls.append((request.path+query,request.get_json(silent=True)))
    def next_state():
        record()
        return queue.pop(0) if queue else {'state':'connected'}
    @app.get('/accounts/<provider>')
    def account(provider):
        return render_template('account_signin.html',provider=provider,asset_v='test',
                               as_token=request.args.get('as',''),return_to=request.args.get('return_to',''))
    @app.get('/account')
    def account_page_landing():return 'Account'
    @app.get('/remote')
    def remote():return render_template('remote.html',asset_v='test')
    @app.post('/alexa/form_login/')
    def amazon_start():return jsonify(next_state())
    @app.post('/alexa/form_login/step/')
    def amazon_step():return jsonify(next_state())
    @app.post('/alexa/form_login/cancel/')
    def amazon_cancel():record();return jsonify(cancelled=True)
    @app.get('/alexa/form_login/captcha/')
    def amazon_captcha():return Response(_png_bytes(),content_type='image/png')
    @app.post('/api/youtube/browser-session/form/<action>')
    def youtube_form(action):
        if action=='stop':record();return jsonify(success=True)
        return jsonify(next_state())
    server=make_server('127.0.0.1',0,app,threaded=True);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    with sync_playwright() as p:
        browser=p.chromium.launch();page=browser.new_page(viewport={'width':390,'height':844},has_touch=True,is_mobile=True)
        yield page,f'http://127.0.0.1:{server.server_port}',calls,queue
        browser.close()
    server.shutdown();thread.join()

@pytest.mark.parametrize('width',[390,1280])
def test_amazon_signin_walks_the_form_and_finishes(account_page,width):
    page,origin,calls,queue=account_page
    queue.extend([
        {'state':'need_code','answer':'securitycode','message':'Enter the two-step code.'},
        {'state':'need_captcha','answer':'captcha','message':'Type the characters you see.'},
        {'state':'connected'},
    ])
    page.set_viewport_size({'width':width,'height':844});page.goto(origin+'/accounts/amazon')
    expect(page.get_by_role('heading',name='Amazon',exact=True)).to_be_visible()
    page.get_by_label('Amazon email or mobile number').fill('owner@example.test');page.get_by_label('Password',exact=True).fill('test-password')
    page.get_by_role('button',name='Continue',exact=True).click()
    expect(page.locator('#code-panel')).to_be_visible()
    expect(page.locator('#password')).to_have_value('')
    page.get_by_label('Code',exact=True).fill('123456');page.locator('#code-submit').click()
    expect(page.locator('#captcha-panel')).to_be_visible()
    expect(page.locator('#captcha-image')).to_be_visible()
    page.get_by_label('Type the characters you see').fill('KL4W9');page.locator('#captcha-submit').click()
    expect(page).to_have_url(origin+'/account?connected=amazon#panel-providers')
    assert calls[0]==('/alexa/form_login/',{'email':'owner@example.test','password':'test-password'})
    assert ('/alexa/form_login/step/',{'kind':'securitycode','value':'123456'}) in calls
    assert ('/alexa/form_login/step/',{'kind':'captcha','value':'KL4W9'}) in calls
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')

def test_amazon_mobile_number_signin_shows_the_one_time_password_step(account_page):
    page,origin,calls,queue=account_page
    queue.extend([
        {'state':'need_code','answer':'verificationcode','label':'One-time password',
         'hint':'Amazon sent it to the phone number or email on your account.',
         'message':'Enter the one-time password Amazon sent you.'},
        {'state':'connected'},
    ])
    page.goto(origin+'/accounts/amazon')
    field=page.get_by_label('Amazon email or mobile number')
    assert field.get_attribute('type')=='text'
    field.fill('+91 90000 00000');page.get_by_label('Password',exact=True).fill('pw')
    page.get_by_role('button',name='Continue',exact=True).click()
    expect(page.locator('#code-panel')).to_be_visible()
    expect(page.get_by_label('One-time password')).to_be_visible()
    expect(page.locator('#code-hint')).to_contain_text('phone number or email')
    page.get_by_label('One-time password').fill('482913');page.locator('#code-submit').click()
    expect(page).to_have_url(origin+'/account?connected=amazon#panel-providers')
    assert calls[0]==('/alexa/form_login/',{'email':'+91 90000 00000','password':'pw'})
    assert ('/alexa/form_login/step/',{'kind':'verificationcode','value':'482913'}) in calls

def test_amazon_error_returns_to_the_form_and_can_restart(account_page):
    page,origin,calls,queue=account_page
    queue.append({'state':'error','message':'The email or password was wrong.'})
    page.goto(origin+'/accounts/amazon')
    page.get_by_label('Amazon email or mobile number').fill('owner@example.test');page.get_by_label('Password',exact=True).fill('nope')
    page.get_by_role('button',name='Continue',exact=True).click()
    expect(page.locator('#status')).to_have_class('error')
    expect(page.locator('#status')).to_contain_text('email or password was wrong')
    expect(page.locator('#setup')).to_be_visible()
    page.get_by_role('button',name='Start over').click()
    expect(page.locator('#status')).to_contain_text('Start again when you are ready.')
    page.get_by_label('Amazon email or mobile number').fill('owner@example.test');page.get_by_label('Password',exact=True).fill('right')
    page.get_by_role('button',name='Continue',exact=True).click()
    expect(page).to_have_url(origin+'/account?connected=amazon#panel-providers')

def test_amazon_choice_options_render_and_submit(account_page):
    page,origin,calls,queue=account_page
    queue.extend([
        {'state':'need_choice','kind':'claimsoption','message':'Choose how Amazon should verify you.',
         'options':[{'index':'0','label':'Send code to +91 90000 00000'},{'index':'1','label':'Email me'}]},
        {'state':'connected'},
    ])
    page.goto(origin+'/accounts/amazon')
    page.get_by_label('Amazon email or mobile number').fill('owner@example.test');page.get_by_label('Password',exact=True).fill('pw')
    page.get_by_role('button',name='Continue',exact=True).click()
    expect(page.locator('#choice-panel')).to_be_visible()
    expect(page.get_by_text('Send code to +91 90000 00000')).to_be_visible()
    page.get_by_text('Email me').click();page.locator('#choice-submit').click()
    assert ('/alexa/form_login/step/',{'kind':'claimsoption','value':'1'}) in calls

def test_amazon_approval_polls_until_connected(account_page):
    page,origin,calls,queue=account_page
    queue.extend([
        {'state':'need_approval','message':'Approve the sign-in on your phone.'},
        {'state':'need_approval','message':'Approve the sign-in on your phone.'},
        {'state':'connected'},
    ])
    page.goto(origin+'/accounts/amazon')
    page.get_by_label('Amazon email or mobile number').fill('owner@example.test');page.get_by_label('Password',exact=True).fill('pw')
    page.get_by_role('button',name='Continue',exact=True).click()
    expect(page.locator('#approval-panel')).to_be_visible()
    expect(page.locator('#status')).to_contain_text('Waiting for the approval')
    expect(page).to_have_url(origin+'/account?connected=amazon#panel-providers',timeout=15000)
    polls=[body for path,body in calls if path=='/alexa/form_login/step/' and body.get('kind')=='poll']
    assert len(polls)>=2

def test_youtube_code_challenge_retries_a_rejected_code(account_page):
    page,origin,calls,queue=account_page
    queue.extend([
        {'state':'need_code','kind':'totp','message':'Enter the code from your authenticator app.'},
        {'state':'need_code','kind':'totp','message':'That code was not accepted. Check it and try again.'},
        {'state':'connected'},
    ])
    page.goto(origin+'/accounts/youtube')
    assert not calls
    page.get_by_label('Google email').fill('owner@gmail.test');page.get_by_label('Password',exact=True).fill('test-password')
    page.get_by_role('button',name='Continue',exact=True).click()
    expect(page.locator('#code-panel')).to_be_visible()
    expect(page.locator('#code-message')).to_contain_text('authenticator app')
    page.get_by_label('Code',exact=True).fill('000000');page.locator('#code-submit').click()
    expect(page.locator('#code-message')).to_contain_text('not accepted')
    page.get_by_label('Code',exact=True).fill('123456');page.locator('#code-submit').click()
    expect(page).to_have_url(origin+'/account?connected=youtube#panel-providers')
    assert ('/api/youtube/browser-session/form/start',{'email':'owner@gmail.test','password':'test-password'}) in calls
    assert ('/api/youtube/browser-session/form/code',{'code':'123456'}) in calls

def test_youtube_prompt_polls_and_cancel_stops_the_browser(account_page):
    page,origin,calls,queue=account_page
    queue.extend([{'state':'need_prompt','message':'Google is waiting for you to approve this sign-in on your phone.'}])
    page.goto(origin+'/accounts/youtube')
    page.get_by_label('Google email').fill('owner@gmail.test');page.get_by_label('Password',exact=True).fill('pw')
    page.get_by_role('button',name='Continue',exact=True).click()
    expect(page.locator('#approval-panel')).to_be_visible()
    expect(page.locator('#approval-message')).to_contain_text('approve this sign-in on your phone')
    page.get_by_role('button',name='Cancel sign-in').click()
    expect(page).to_have_url(origin+'/account#panel-providers')
    assert ('/api/youtube/browser-session/form/stop',{}) in calls

def test_youtube_as_token_rides_along_for_admin_driven_signins(account_page):
    page,origin,calls,queue=account_page
    queue.append({'state':'connected'})
    page.goto(origin+'/accounts/youtube?as=signed-admin-token&return_to=%2Fadmin%2Fusers%2Fx%2F')
    page.get_by_label('Google email').fill('user@gmail.test');page.get_by_label('Password',exact=True).fill('pw')
    page.get_by_role('button',name='Continue',exact=True).click()
    expect(page).to_have_url(origin+'/admin/users/x/',timeout=10000)
    start=[(path,body) for path,body in calls if path.split('?')[0].endswith('/form/start')][0]
    assert 'as=signed-admin-token' in start[0]


def test_readonly_controls_explain_block_and_restore_after_handoff(account_page):
    page,_,_,_=account_page
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
    page,_,_,_=account_page
    page.set_content('<button id="profile-menu-trigger" class="profile-menu-trigger">Profile</button>')
    page.add_style_tag(content=(ROOT/'templates/static/css/sidebar.css').read_text())
    page.evaluate('window.api=async()=>({alexa_access_enabled:true,amazon_connected:false})')
    code=(ROOT/'templates/static/js/sidebar.js').read_text().split('// Connection health is independent')[1]
    page.evaluate('// Connection health is independent'+code)
    expect(page.locator('#profile-menu-trigger')).to_have_class('profile-menu-trigger amazon-disconnected')
    assert page.locator('#profile-menu-trigger').evaluate('e=>getComputedStyle(e).borderTopColor')=='rgb(241, 111, 101)'
    page.evaluate('setAmazonConnection(true)');expect(page.locator('#profile-menu-trigger')).not_to_have_class('profile-menu-trigger amazon-disconnected')


def test_sidebar_device_list_fades_and_blocks_logged_out_echo(account_page):
    page,origin,_,_=account_page
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


"""Exercise the inline login state machine at phone and desktop sizes."""
from pathlib import Path
import threading
import pytest
from flask import Flask, render_template, request, jsonify
from werkzeug.serving import make_server
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[2]

@pytest.fixture
def login_page():
    app = Flask(__name__, template_folder=str(ROOT/'templates'), static_folder=str(ROOT/'templates/static'))
    calls = []
    @app.route('/login/', methods=['GET', 'POST'])
    def login():
        if request.method == 'GET':
            return render_template('login.html', asset_v='test', next_target='/home',
                                   signup_open=True, forgot_url='/forgot-password', otp_available=True,
                                   otp_start=request.args.get('otp') == '1', code_ready=request.args.get('code') == '1')
        body = request.get_json(); calls.append(body)
        if body.get('step') == 'send_otp':
            return jsonify(ok=True, retry_after=60, message='Check your inbox.')
        return jsonify(ok=True, next='/home')
    @app.get('/home')
    def home(): return 'Signed in'
    server = make_server('127.0.0.1', 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        yield page, f'http://127.0.0.1:{server.server_port}', calls
        browser.close()
    server.shutdown(); thread.join()

@pytest.mark.parametrize('width', [390, 1280])
def test_inline_otp_flow_and_cooldown(login_page, width):
    page, origin, calls = login_page
    page.set_viewport_size({'width': width, 'height': 844})
    page.goto(origin+'/login/')
    expect(page.get_by_role('link', name='Enter your code')).to_have_count(0)
    page.locator('#username').fill('person@example.test')
    page.get_by_role('button', name='Login via OTP', exact=True).click()
    expect(page.locator('#password')).not_to_be_visible()
    expect(page.locator('#creds-btn')).to_be_disabled()
    page.get_by_role('button', name='Send OTP', exact=True).click()
    expect(page.locator('#email-code')).to_be_visible()
    expect(page.locator('#send-otp')).not_to_be_visible()
    expect(page.locator('#resend-otp')).to_be_disabled()
    expect(page.locator('#resend-otp')).to_contain_text('Resend (')
    page.locator('#email-code').fill('12345')
    expect(page.locator('#creds-btn')).to_be_disabled()
    page.locator('#email-code').fill('012345')
    expect(page.locator('#creds-btn')).to_be_enabled()
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.locator('#creds-btn').click()
    page.wait_for_url(origin+'/home')
    assert calls[-1]['step'] == 'email_otp'
    assert calls[-1]['code'] == '012345'
    assert len([call for call in calls if call.get('step') == 'send_otp']) == 1


def test_switch_back_preserves_password_login(login_page):
    page, origin, calls = login_page
    page.goto(origin+'/login/')
    page.get_by_role('button', name='Login via OTP', exact=True).click()
    page.locator('#username').fill('person')
    page.get_by_role('button', name='Send OTP', exact=True).click()
    page.locator('#email-code').fill('123')
    page.get_by_role('button', name='Login via password', exact=True).click()
    expect(page.locator('#password')).to_be_visible()
    page.locator('#username').fill('person')
    page.locator('#password').fill('secret')
    page.locator('#creds-btn').click()
    page.wait_for_url(origin+'/home')
    assert calls[-1]['password'] == 'secret'
    assert 'step' not in calls[-1]


def test_old_code_page_destination_opens_inline_entry(login_page):
    page, origin, calls = login_page
    page.goto(origin+'/login/?otp=1&code=1')
    expect(page.locator('#email-code')).to_be_visible()
    expect(page.locator('#password')).not_to_be_visible()
    expect(page.locator('#creds-btn')).to_be_disabled()
    page.locator('#username').fill('new-person')
    page.locator('#email-code').fill('123456')
    expect(page.locator('#creds-btn')).to_be_enabled()
    page.locator('#creds-btn').click()
    page.wait_for_url(origin+'/home')
    assert calls[-1]['username'] == 'new-person'
