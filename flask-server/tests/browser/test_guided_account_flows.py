"""Real security endpoints behind one-step-at-a-time account forms."""
from pathlib import Path
import sys
import threading
import pytest
from flask import render_template
from playwright.sync_api import sync_playwright, expect
from werkzeug.serving import make_server
ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT/'tests')]
from test_selfservice import SelfServiceTestBase, PASSWORD, NEW_PASSWORD


@pytest.fixture
def guided_page():
    case = SelfServiceTestBase(); case.setUp()
    case.app.template_folder = str(ROOT/'templates')
    case.app.static_folder = str(ROOT/'templates/static')
    owner = case.accounts.get_user_by_username('owner')
    user = case.accounts.create_user(owner, 'guided-listener', PASSWORD, email='listener@example.test', email_verified=True)
    @case.app.route('/welcome')
    def welcome():
        return render_template('landing.html',asset_v='test',admin_login_url='/admin/login/')
    case.sign_in(user)
    server = make_server('0.0.0.0', 0, case.app, threaded=True)
    worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
    origin = 'http://127.0.0.1:'+str(server.server_port)
    with sync_playwright() as driver:
        browser = driver.chromium.launch(headless=True)
        context = browser.new_context(viewport={'width':1280,'height':800})
        context.add_cookies([{'name':'session','value':case.client.get_cookie('session').value,'url':origin}])
        page = context.new_page(); errors=[]
        page.on('pageerror', lambda error: errors.append(str(error)))
        try:
            yield page, case, user, origin
            assert not errors, errors
        finally:
            browser.close(); server.shutdown(); worker.join(timeout=3); server.server_close(); case.tearDown()


@pytest.mark.parametrize('width',[320,390,1280,1920])
def test_landing_login_actions_and_layout(guided_page,width):
    page, case, user, origin = guided_page
    page.set_viewport_size({'width':width,'height':800})
    page.goto(origin+'/welcome')
    expect(page.locator('header').get_by_role('link',name='Admin login')).to_have_attribute('href','/admin/login/')
    expect(page.locator('header').get_by_role('link',name='User login')).to_have_attribute('href','/login/')
    assert page.locator('h1').bounding_box()['y'] < 800
    assert page.get_by_role('link',name='Start listening').bounding_box()['y'] < 800
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.screenshot(path=f'/tmp/musicbox-redesign/landing-{width}.png',full_page=True)


def test_reset_reveals_password_only_after_server_verifies_code(guided_page):
    page, case, user, origin = guided_page
    page.goto(origin+'/account?step=reset')
    dialog=page.locator('#dialog-password')
    expect(dialog.locator('#reset_password')).not_to_be_visible()
    dialog.locator('#reset-send').click()
    expect(dialog.locator('#reset-send')).to_contain_text('Resend in')
    code=case.newest_code('reset')
    dialog.locator('#reset_code').fill('999999' if code!='999999' else '888888')
    dialog.get_by_role('button',name='Verify and continue').click()
    expect(dialog.locator('.field-error:visible').last).to_contain_text('incorrect or expired')
    expect(dialog.locator('#reset_password')).not_to_be_visible()
    dialog.locator('#reset_code').fill(code)
    dialog.get_by_role('button',name='Verify and continue').click()
    expect(dialog.locator('#reset_password')).to_be_visible()
    expect(dialog.locator('#reset_code')).not_to_be_visible()
    dialog.locator('#reset_password').fill(NEW_PASSWORD)
    dialog.locator('#reset_confirm').fill(NEW_PASSWORD)
    dialog.get_by_role('button',name='Set the new password').click()
    expect(dialog).not_to_be_visible()
    assert not case.accounts.email_code_state(user['id'],'reset')['pending']


def test_email_change_then_new_address_confirmation(guided_page):
    page, case, user, origin = guided_page
    page.goto(origin+'/account?step=email')
    dialog=page.locator('#dialog-email')
    expect(dialog.locator('#profile_email')).not_to_be_visible()
    dialog.locator('#reauth-send').click()
    expect(dialog.locator('#reauth-send')).to_contain_text('Resend in')
    dialog.locator('#email_change_code').fill(case.newest_code('reauth'))
    dialog.get_by_role('button',name='Verify and continue').click()
    expect(dialog.locator('#profile_email')).to_be_visible()
    dialog.locator('#profile_email').fill('new-listener@example.test')
    dialog.locator('[name=current_password]').fill(PASSWORD)
    dialog.get_by_role('button',name='Change the address').click()
    confirm=page.locator('#dialog-verify-email')
    expect(confirm).to_be_visible()
    confirm.locator('#email_code').fill(case.newest_code('verify'))
    confirm.get_by_role('button',name='Confirm my email').click()
    expect(confirm).not_to_be_visible()
    page.wait_for_url('**/login/?email_changed=1')
    assert case.accounts.get_user(user['id'])['email'] == 'new-listener@example.test'
    assert case.accounts.get_user(user['id'])['email_verified']


def test_password_step_closing_clears_secrets_and_resets_flow(guided_page):
    page, case, user, origin = guided_page
    page.goto(origin+'/account?step=password')
    dialog=page.locator('#dialog-password')
    expect(dialog.locator('#pw_new')).not_to_be_visible()
    dialog.locator('#change-send').click()
    expect(dialog.locator('#change-send')).to_contain_text('Resend in')
    dialog.locator('#change-code').fill(case.newest_code('reset'))
    dialog.get_by_role('button',name='Verify and continue').click()
    expect(dialog.locator('#pw_new')).to_be_visible()
    dialog.locator('#pw_new').fill(NEW_PASSWORD)
    page.keyboard.press('Escape')
    page.get_by_role('link',name='Change password',exact=True).click()
    expect(dialog.locator('#pw_new')).not_to_be_visible()
    expect(dialog.locator('#change-code')).to_have_value('')


def test_public_reset_has_verified_password_step(guided_page):
    page, case, user, origin = guided_page
    code=case.accounts.create_email_code(user['id'],'reset',user['email'])
    page.goto(origin+'/reset-password')
    expect(page.locator('#password')).not_to_be_visible()
    page.locator('#identifier').fill(user['username'])
    page.locator('#code').fill(code)
    page.get_by_role('button',name='Verify code',exact=True).click()
    expect(page.locator('#password')).to_be_visible()
    page.locator('#password').fill(NEW_PASSWORD)
    page.locator('#confirm_password').fill(NEW_PASSWORD)
    page.get_by_role('button',name='Change password',exact=True).click()
    expect(page.get_by_text('Password changed',exact=True)).to_be_visible()


def test_totp_email_proof_precedes_password_and_setup(guided_page):
    page, case, user, origin = guided_page
    page.goto(origin+'/account?step=totp')
    dialog=page.locator('#dialog-totp')
    expect(dialog.locator('#enroll')).not_to_be_visible()
    dialog.locator('#reauth-send-begin').click()
    expect(dialog.locator('#reauth-send-begin')).to_contain_text('Resend in')
    dialog.locator('#begin_email_code').fill(case.newest_code('reauth'))
    dialog.get_by_role('button',name='Verify and continue').click()
    dialog.locator('#form-totp-begin [name=current_password]').fill(PASSWORD)
    dialog.locator('#form-totp-begin [type=submit]').click()
    expect(dialog.locator('#enroll')).to_be_visible()
    expect(dialog.locator('#totp-begin-block')).not_to_be_visible()
