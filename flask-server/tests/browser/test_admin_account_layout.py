"""Single-purpose owner actions, bounded device lists and matching popup chrome."""
from pathlib import Path
import pytest
from playwright.sync_api import expect
from test_admin_user_detail import admin_site, admin_page


def test_profile_has_one_edit_action_and_logs_in_header(admin_page):
    page,case,user,origin,errors=admin_page
    expect(page.locator('[data-dialog-open="edit-user-dialog"]')).to_have_count(1)
    header=page.locator('.ud-hero-actions')
    expect(header.get_by_role('link',name='Logs',exact=True)).to_be_visible()
    expect(header.get_by_role('button',name='Edit profile',exact=True)).to_be_visible()
    assert page.locator('.ud-main > a').count()==0


@pytest.mark.parametrize('width',[1024,1440])
def test_desktop_columns_align_and_devices_scroll_inside_card(admin_page,width):
    page,case,user,origin,errors=admin_page
    case.layer.db.execute("UPDATE users SET status='active' WHERE id=?",(user['id'],))
    for i in range(14):
        case.accounts.create_session(case.accounts.get_user(user['id']),ip='192.0.2.'+str(i+1),user_agent='Chrome fixture '+str(i))
    page.set_viewport_size({'width':width,'height':1000})
    page.reload()
    expect(page.locator('.ud-device')).to_have_count(14)
    facts=page.evaluate('''() => {
      const main=document.querySelector('.ud-main').getBoundingClientRect();
      const aside=document.querySelector('.ud-aside').getBoundingClientRect();
      const danger=document.querySelector('#danger-zone');
      const list=document.querySelector('.ud-device-list');
      return {mainBottom:main.bottom,asideBottom:aside.bottom,deleteOnLeft:danger.closest('.ud-main')!==null,
              listHeight:list.clientHeight,scrollHeight:list.scrollHeight,overflow:getComputedStyle(list).overflowY};
    }''')
    assert abs(facts['mainBottom']-facts['asideBottom'])<=1,facts
    assert facts['deleteOnLeft'],facts
    assert facts['scrollHeight']>facts['listHeight'],facts
    assert facts['listHeight']<=400,facts
    assert facts['overflow']=='auto',facts
    page.locator('.ud-device-list').evaluate('(node)=>node.scrollTop=node.scrollHeight')
    page.locator('.ud-device').last.get_by_role('button',name='Details').click()
    expect(page.locator('#session-info-14')).to_be_visible()
    directory=Path('/tmp/musicbox-admin-refinement');directory.mkdir(exist_ok=True)
    page.keyboard.press('Escape')
    page.evaluate('window.scrollTo(0,0)')
    page.screenshot(path=str(directory/f'account-{width}.png'),full_page=True)


def test_password_options_each_open_one_focused_popup(admin_page):
    page,case,user,origin,errors=admin_page
    opener=page.get_by_role('button',name='Password options',exact=True)
    opener.click()
    choose=page.locator('#password-user-dialog')
    expect(choose.locator('form')).to_have_count(0)
    page.screenshot(path='/tmp/musicbox-admin-refinement/password-options.png')
    choose.get_by_role('button',name='Reset password now').click()
    reset=page.locator('#password-reset-dialog')
    expect(choose).not_to_be_visible()
    expect(reset).to_be_visible()
    expect(page.locator('dialog[open]')).to_have_count(1)
    expect(reset.locator('#reset-password')).not_to_be_visible()
    expect(reset.locator('#reset-password')).to_be_disabled()
    reset.get_by_label('Set a password yourself').check()
    expect(reset.locator('#reset-password')).to_be_visible()
    expect(reset.locator('#reset-password')).to_be_enabled()
    expect(reset.locator('#reset-password')).to_have_attribute('required','')
    page.screenshot(path='/tmp/musicbox-admin-refinement/password-reset.png')
    reset.locator('#reset-password').fill('Replacement!123')
    reset.get_by_label('Generate a secure password').check()
    expect(reset.locator('#reset-password')).to_have_value('')
    expect(reset.locator('#reset-password')).not_to_be_visible()
    reset.get_by_role('button',name='Back',exact=True).click()
    expect(choose).to_be_visible()
    choose.get_by_role('button',name='Require a password change',exact=False).click()
    require=page.locator('#password-require-dialog')
    expect(require).to_be_visible()
    expect(choose).not_to_be_visible()
    expect(require.locator('input[type=password]')).to_have_count(0)
    page.keyboard.press('Escape')
    expect(opener).to_be_focused()


def test_require_change_saves_without_resetting_password(admin_page):
    page,case,user,origin,errors=admin_page
    before=case.layer.db.scalar('SELECT password_hash FROM users WHERE id=?',(user['id'],))
    page.get_by_role('button',name='Password options',exact=True).click()
    page.locator('#password-user-dialog').get_by_role('button',name='Require a password change',exact=False).click()
    page.locator('#password-require-dialog').get_by_role('button',name='Require change',exact=True).click()
    assert case.accounts.get_user(user['id'])['must_change_password']
    assert case.layer.db.scalar('SELECT password_hash FROM users WHERE id=?',(user['id'],))==before
    page.get_by_role('button',name='Password options',exact=True).click()
    page.locator('#password-user-dialog').get_by_role('button',name='Remove change requirement',exact=False).click()
    page.locator('#password-require-dialog').get_by_role('button',name='Remove requirement',exact=True).click()
    assert not case.accounts.get_user(user['id'])['must_change_password']


def test_only_current_token_prefix_is_visible_without_history(admin_page):
    page,case,user,origin,errors=admin_page
    case.layer.db.execute("UPDATE users SET status='active',email_verified=1,alexa_enabled=1 WHERE id=?",(user['id'],))
    previous=case.layer.alexa_access.rotate(user['id'])
    active=case.layer.alexa_access.rotate(user['id'])
    page.reload()
    expect(page.locator('#active-alexa-token')).to_contain_text(active[:12]+'…')
    expect(page.get_by_role('button',name='Rotate token',exact=True)).to_be_visible()
    expect(page.get_by_role('button',name='Revoke token',exact=True)).to_be_visible()
    expect(page.locator('#token-history-dialog')).to_have_count(0)
    assert previous[:12] not in page.content()
    assert active.split('.')[0] not in page.content()
    page.screenshot(path="/tmp/musicbox-single-token-admin.png", full_page=True)
