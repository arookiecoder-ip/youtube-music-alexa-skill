"""The redesigned settings retain readable, contained rows in both themes."""
from playwright.sync_api import expect
import pytest
from test_guided_account_flows import guided_page
from test_admin_user_detail import admin_site, admin_page


@pytest.mark.parametrize('width',[390,1280])
@pytest.mark.parametrize('theme',['dark','light'])
def test_account_settings_fit_both_themes(guided_page,width,theme):
    page,case,user,origin=guided_page
    page.set_viewport_size({'width':width,'height':900})
    page.goto(origin+'/account')
    page.locator('html').evaluate('(node,theme)=>node.dataset.theme=theme',theme)
    expect(page.locator('#title-profile')).to_be_visible()
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    rows=page.locator('#panel-profile .state-line').evaluate_all('(nodes)=>nodes.map(n=>{const b=n.getBoundingClientRect();return {left:b.left,right:b.right}})')
    assert all(row['left']>=0 and row['right']<=width for row in rows)
    page.screenshot(path=f'/tmp/musicbox-redesign/account-{theme}-{width}.png',full_page=True)


def test_user_directory_uses_shared_theme_and_no_admin_role(admin_page):
    page,case,user,origin,errors=admin_page
    page.goto(origin+'/admin/users/')
    expect(page.locator('#user-role option')).to_have_text(['All roles','owner','user'])
    expect(page.get_by_role('link',name='Create user')).to_be_visible()
    page.screenshot(path='/tmp/musicbox-redesign/admin-users.png',full_page=True)


def test_account_id_can_be_copied_without_exposing_credentials(guided_page):
    page,case,user,origin=guided_page
    page.context.grant_permissions(['clipboard-read', 'clipboard-write'])
    page.goto(origin+'/account')
    expect(page.locator('#account-id')).to_have_text(user['id'])
    page.locator('#copy-account-id').click()
    expect(page.locator('#copy-account-id')).to_have_text('Copied')
    assert page.evaluate('navigator.clipboard.readText()') == user['id']
