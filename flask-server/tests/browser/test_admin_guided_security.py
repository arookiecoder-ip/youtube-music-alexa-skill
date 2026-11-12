"""Owner account forms share server-verified steps with the user settings."""
from playwright.sync_api import expect
from test_admin_user_detail import admin_site, admin_page


def test_owner_password_fields_follow_identity_check(admin_page):
    page, case, user, origin, errors = admin_page
    page.goto(origin+'/admin/account/')
    page.get_by_role('button',name='Change password',exact=True).click()
    dialog=page.locator('#password-dialog')
    expect(dialog.locator('#pw_new')).not_to_be_visible()
    dialog.locator('#pw_current').fill(case.admin_password)
    dialog.get_by_role('button',name='Verify and continue').click()
    expect(dialog.locator('#pw_new')).to_be_visible()
    page.keyboard.press('Escape')
    page.get_by_role('button',name='Change password',exact=True).click()
    expect(dialog.locator('#pw_current')).to_have_value('')
    expect(dialog.locator('#pw_new')).not_to_be_visible()


def test_user_detail_only_links_to_logs(admin_page):
    page, case, user, origin, errors = admin_page
    expect(page.get_by_role('link',name='Logs',exact=True)).to_have_attribute('href', '/admin/audit/?target='+user['id'])
    assert page.locator('.ud-timeline').count() == 0
