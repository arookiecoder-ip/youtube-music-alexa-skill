from flask import session
from auth_sessions import create_browser_session, replace_identity
from test_multiuser import MultiUserTestBase


class BrowserDeviceSessionTests(MultiUserTestBase):
    def setUp(self):
        super().setUp()
        self.accounts = self.layer.accounts
        self.owner = self.accounts.get_user_by_username('owner')
        @self.app.post('/test-device-login')
        def login():
            raw = create_browser_session(self.accounts, self.owner, 'same-ip', 'same-browser')
            replace_identity()
            session['mu_sid'] = raw
            return {'ok': True}

    def token(self, client):
        with client.session_transaction() as browser: return browser['mu_sid']

    def test_relogin_rotates_and_revokes_previous_session(self):
        self.client.post('/test-device-login')
        first = self.token(self.client)
        response = self.client.post('/test-device-login')
        second = self.token(self.client)
        self.assertNotEqual(first, second)
        self.assertEqual(self.accounts.validate_session(first), (None, None))
        self.assertIsNotNone(self.accounts.validate_session(second)[0])
        self.assertIn('HttpOnly', response.headers.get('Set-Cookie'))
        self.assertEqual(self.layer.db.scalar('SELECT COUNT(*) FROM user_sessions WHERE revoked_at=0'), 1)

    def test_same_user_agent_and_ip_on_another_browser_stays_independent(self):
        other = self.app.test_client()
        self.client.post('/test-device-login')
        first = self.token(self.client)
        other.post('/test-device-login')
        self.assertIsNotNone(self.accounts.validate_session(first)[0])
        self.assertEqual(self.layer.db.scalar('SELECT COUNT(*) FROM user_sessions WHERE revoked_at=0'), 2)

    def test_device_identity_survives_authentication_cookie_clear(self):
        self.client.post('/test-device-login')
        first = self.token(self.client)
        with self.client.session_transaction() as browser: browser.clear()
        self.client.post('/test-device-login')
        self.assertEqual(self.accounts.validate_session(first), (None, None))
        self.assertEqual(self.layer.db.scalar('SELECT COUNT(*) FROM user_sessions WHERE revoked_at=0'), 1)

    def test_tampered_identity_does_not_revoke_another_device(self):
        self.client.post('/test-device-login')
        first = self.token(self.client)
        other = self.app.test_client()
        other.set_cookie('musicbox_device', 'forged')
        other.post('/test-device-login')
        self.assertIsNotNone(self.accounts.validate_session(first)[0])

    def test_domain_rotation_cannot_cross_accounts_and_adopts_legacy_sid(self):
        user = self.accounts.create_user(self.owner, 'another', 'Another!Passw0rd12')
        owner_sid = self.accounts.create_session(self.owner, device_id='shared-install')
        self.accounts.create_session(user, device_id='shared-install', previous_sid=owner_sid)
        self.assertIsNotNone(self.accounts.validate_session(owner_sid)[0])
        legacy = self.accounts.create_session(user)
        self.accounts.create_session(user, device_id='new-install', previous_sid=legacy)
        self.assertEqual(self.accounts.validate_session(legacy), (None, None))
