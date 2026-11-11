"""Real admin routes: user hierarchy, modal actions, CSRF and responsive layout."""
import os
from pathlib import Path
import sys
import threading

import pytest
from playwright.sync_api import sync_playwright, expect
from werkzeug.serving import make_server

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
from test_login_second_factor import SecondFactorDoorTestBase


@pytest.fixture
def admin_site():
    case = SecondFactorDoorTestBase()
    case.setUp()
    case.layer_app.static_folder = str(ROOT / 'templates/static')
    user = case.player_user('testabc')
    case.layer.db.execute("UPDATE users SET status='invited',email_verified=0 WHERE id=?", (user['id'],))
    server = make_server('127.0.0.1', 0, case.layer_app, threaded=True)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield case, user, 'http://127.0.0.1:' + str(server.server_port)
    finally:
        server.shutdown()
        worker.join(timeout=3)
        server.server_close()
        case.tearDown()


@pytest.fixture
def admin_page(admin_site):
    case, user, origin = admin_site
    with sync_playwright() as driver:
        browser = driver.chromium.launch(headless=True)
        context = browser.new_context(viewport={'width': 1440, 'height': 1100})
        context.grant_permissions(['clipboard-read', 'clipboard-write'], origin=origin)
        page = context.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(origin + '/admin/login/')
        page.locator('input[name=username]').fill('owner')
        page.locator('input[name=password]').fill(case.admin_password)
        page.locator('button[type=submit]').click()
        page.wait_for_url('**/admin/')
        page.goto(origin + '/admin/users/' + user['id'] + '/')
        yield page, case, user, origin, errors
        assert not errors, errors
        context.close()
        browser.close()


@pytest.mark.parametrize('width', [320, 375, 768, 1024, 1440])
@pytest.mark.parametrize('theme', ['dark', 'light'])
def test_user_page_hierarchy_and_responsive_layout(admin_page, width, theme):
    page, case, user, origin, errors = admin_page
    page.set_viewport_size({'width': width, 'height': 1100})
    # Theme only changes presentation here; the real server renders the same variables.
    if width <= 860:
        expect(page.locator('.sidebar')).not_to_be_visible()
    page.locator('html').evaluate('(node, theme) => node.dataset.theme = theme', theme)
    expect(page.locator('.ud-hero h1')).to_have_text('testabc')
    expect(page.locator('.ud-summary')).to_contain_text('Not verified')
    expect(page.locator('.ud-notice').first).to_contain_text('Waiting for invitation')
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    boxes = page.locator('.ud-panel:visible').evaluate_all('(nodes) => nodes.map(n => {const b=n.getBoundingClientRect();return {left:b.left,right:b.right,width:b.width}})')
    if width <= 600:
        assert max(b['width'] for b in boxes) - min(b['width'] for b in boxes) <= 1
    assert all(b['left'] >= 0 and b['right'] <= width + 1 for b in boxes), boxes
    assert page.locator('input[name=username]:visible').count() == 0
    directory = Path(os.environ.get('ADMIN_USER_SCREENSHOTS', '/tmp/admin-user-redesign'))
    directory.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(directory / ('user-' + theme + '-' + str(width) + '.png')), full_page=True)


def test_profile_popup_saves_real_form_and_restores_focus(admin_page):
    page, case, user, origin, errors = admin_page
    page.get_by_role('button', name='Edit profile', exact=True).click()
    dialog = page.locator('#edit-user-dialog')
    expect(dialog).to_be_visible()
    page.screenshot(path='/tmp/admin-user-redesign/profile-dialog.png')
    dialog.locator('#username').fill('edited-user')
    dialog.get_by_role('button', name='Save changes').click()
    expect(page.locator('.ud-hero h1')).to_have_text('edited-user')
    assert case.accounts.get_user(user['id'])['username'] == 'edited-user'
    page.get_by_role('button', name='Edit profile', exact=True).click()
    page.keyboard.press('Escape')
    expect(dialog).not_to_be_visible()
    expect(page.get_by_role('button', name='Edit profile', exact=True)).to_be_focused()


def test_alexa_access_requires_confirmation_and_shows_eligibility(admin_page):
    page, case, user, origin, errors = admin_page
    page.get_by_role('button', name='Enable access', exact=True).click()
    dialog = page.locator('#admin-dialog')
    expect(dialog).to_be_visible()
    expect(dialog).to_contain_text('active account')
    dialog.get_by_role('button', name='Cancel', exact=True).click()
    assert not case.accounts.get_user(user['id'])['alexa_enabled']
    page.get_by_role('button', name='Enable access', exact=True).click()
    dialog.get_by_role('button', name='Enable access', exact=True).click()
    expect(page.locator('.ud-state')).to_contain_text('Allowed')
    expect(page.get_by_role('button', name='Generate token', exact=True)).to_be_disabled()
    expect(page.locator('.ud-token-row')).to_contain_text('Activate the account')
    assert case.accounts.get_user(user['id'])['alexa_enabled']


def test_new_token_has_private_reveal_popup_and_copy_action(admin_page):
    page, case, user, origin, errors = admin_page
    case.layer.db.execute("UPDATE users SET status='active',email_verified=1,alexa_enabled=1 WHERE id=?", (user['id'],))
    page.reload()
    page.get_by_role('button', name='Generate token', exact=True).click()
    dialog = page.locator('#token-create-dialog')
    expect(dialog).to_be_visible()
    expect(dialog).to_contain_text('Previous tokens will stop working')
    dialog.get_by_role('button', name='Generate token', exact=True).click()
    reveal = page.locator('#token-reveal-dialog')
    expect(reveal).to_be_visible()
    token = reveal.locator('textarea').input_value()
    assert token.startswith('mbx_')
    page.screenshot(path='/tmp/admin-user-redesign/token-dialog.png')
    reveal.get_by_role('button', name='Copy token', exact=True).click()
    expect(reveal.locator('[role=status]')).to_contain_text('Token copied')
    assert page.evaluate('navigator.clipboard.readText()') == token
    reveal.get_by_role('button', name='I have saved it', exact=True).click()
    page.reload()
    assert token not in page.content()
    expect(page.get_by_role('button', name='Rotate token', exact=True)).to_be_enabled()


def test_security_actions_open_separate_dialogs_and_delete_can_be_cancelled(admin_page):
    page, case, user, origin, errors = admin_page
    page.locator('.ud-security-item').filter(has_text='Email verification').get_by_role('button', name='Manage').click()
    expect(page.locator('#email-user-dialog')).to_be_visible()
    expect(page.get_by_role('button', name='Resend invitation')).to_be_visible()
    page.keyboard.press('Escape')
    page.locator('.ud-security-item').filter(has_text='Password').get_by_role('button', name='Manage').click()
    expect(page.locator('#password-user-dialog')).to_be_visible()
    page.keyboard.press('Escape')
    page.get_by_role('button', name='Delete user', exact=True).click()
    expect(page.locator('#admin-dialog')).to_be_visible()
    page.locator('#admin-dialog').get_by_role('button', name='Cancel').click()
    assert case.accounts.get_user(user['id'])['status'] == 'invited'


def test_users_directory_keeps_filters_and_links_usable(admin_page):
    page, case, user, origin, errors = admin_page
    page.goto(origin + '/admin/users/')
    page.locator('#user-q').fill('testabc')
    page.get_by_role('button', name='Filter', exact=True).click()
    expect(page.locator('tbody')).to_contain_text('testabc')
    page.get_by_role('link', name='Manage', exact=True).click()
    expect(page.locator('.ud-hero h1')).to_have_text('testabc')


def test_mobile_dialog_and_section_navigation(admin_page):
    page, case, user, origin, errors = admin_page
    page.set_viewport_size({'width': 375, 'height': 800})
    page.get_by_role('link', name='Security', exact=True).click()
    expect(page.locator('#security-title')).to_be_in_viewport()
    page.locator('.ud-security-item').filter(has_text='Password').get_by_role('button', name='Manage').click()
    dialog = page.locator('#password-user-dialog')
    expect(dialog).to_be_visible()
    assert dialog.evaluate('(node) => {const b=node.getBoundingClientRect();return b.left>=0 && b.right<=innerWidth && b.top>=0 && b.bottom<=innerHeight}')
    page.keyboard.press('Escape')
    expect(dialog).not_to_be_visible()


def test_confirmation_preserves_the_clicked_buttons_action(admin_page):
    page, case, user, origin, errors = admin_page
    page.evaluate('''() => {
      const source=document.querySelector('form[action$="/alexa/"]');
      const form=document.createElement('form');
      form.method='post'; form.setAttribute('action', source.getAttribute('action'));
      form.dataset.confirm='Apply the selected action?'; form.dataset.confirmButton='Submit action';
      form.append(source.querySelector('[name="_csrf"]').cloneNode(true));
      const enabled=document.createElement('input'); enabled.type='hidden'; enabled.name='enabled'; enabled.value='1'; form.append(enabled);
      const button=document.createElement('button'); button.type='submit'; button.name='action'; button.value='access'; button.textContent='Submitter regression'; form.append(button);
      document.querySelector('.user-detail').append(form);
    }''')
    page.get_by_role('button', name='Submitter regression').click()
    page.locator('#admin-dialog').get_by_role('button', name='Submit action').click()
    expect(page.locator('.ud-state')).to_contain_text('Allowed')
    assert case.accounts.get_user(user['id'])['alexa_enabled']


def test_deleted_user_has_restore_action_and_no_edit_controls(admin_page):
    page, case, user, origin, errors = admin_page
    case.layer.db.execute("UPDATE users SET status='deleted' WHERE id=?", (user['id'],))
    page.reload()
    expect(page.get_by_role('heading', name='This account is deleted')).to_be_visible()
    expect(page.get_by_role('button', name='Restore account', exact=True)).to_be_visible()
    expect(page.get_by_role('button', name='Edit profile', exact=True)).not_to_be_visible()
    expect(page.locator('#edit-user-dialog')).not_to_be_attached()


def test_expired_sessions_are_not_counted_as_active(admin_page):
    page, case, user, origin, errors = admin_page
    case.layer.db.execute("UPDATE users SET status='active' WHERE id=?", (user['id'],))
    case.accounts.create_session(case.accounts.get_user(user['id']), ip='127.0.0.1', user_agent='Chrome test')
    case.layer.db.execute('UPDATE user_sessions SET expires_at=1 WHERE user_id=?', (user['id'],))
    page.reload()
    expect(page.locator('#sessions-title .ud-count')).to_have_text('0')
    expect(page.locator('.ud-device .badge')).to_have_text('Expired')
    page.locator('.ud-device').get_by_role('button', name='Details').click()
    dialog = page.locator('#session-info-1')
    expect(dialog).to_be_visible()
    expect(dialog).to_contain_text('127.0.0.1')
    expect(dialog).to_contain_text('Expired')
    dialog.get_by_role('button', name='Done').click()


def test_provider_disconnect_is_confirmed_before_removing_a_connection(admin_page):
    page, case, user, origin, errors = admin_page
    case.accounts.set_provider_credentials(case.owner, user['id'], 'youtube', {'synthetic': 'fixture'})
    page.reload()
    expect(page.locator('.ud-provider')).to_contain_text('YouTube Music')
    page.locator('.ud-provider').get_by_role('button', name='Disconnect', exact=True).click()
    expect(page.locator('#admin-dialog')).to_contain_text('saved youtube session')
    page.locator('#admin-dialog').get_by_role('button', name='Cancel', exact=True).click()
    assert len(case.accounts.provider_status(user['id'])) == 1


def test_alexa_token_revoke_and_disable_have_distinct_confirmations(admin_page):
    page, case, user, origin, errors = admin_page
    case.layer.db.execute("UPDATE users SET status='active',email_verified=1,alexa_enabled=1 WHERE id=?", (user['id'],))
    token = case.layer.alexa_access.rotate(user['id'])
    page.reload()
    page.locator('.ud-token-history summary').click()
    page.get_by_role('button', name='Revoke all tokens').click()
    expect(page.locator('#admin-dialog')).to_contain_text('every existing Alexa token')
    page.locator('#admin-dialog').get_by_role('button', name='Revoke tokens', exact=True).click()
    expect(page.locator('.ud-token-record .badge')).to_have_text('revoked')
    page.get_by_role('button', name='Disable access', exact=True).click()
    expect(page.locator('#admin-dialog')).to_contain_text('Hide Amazon options')
    page.locator('#admin-dialog').get_by_role('button', name='Disable access', exact=True).click()
    expect(page.locator('.ud-state')).to_contain_text('Off')
    assert not case.layer.alexa_access.enabled(user['id'])
