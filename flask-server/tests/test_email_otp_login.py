"""Real account storage and player login route; no SMTP or external services."""
import time
from unittest import mock
from tests.test_login_second_factor import SecondFactorDoorTestBase, PLAYER_UA, CLIENT_IP, PLAYER_PASSWORD
from multiuser.security import totp_at


class EmailOtpLoginTests(SecondFactorDoorTestBase):
    def setUp(self):
        super().setUp()
        self.bind_player_door()
        self.user = self.player_user('email-player')

    def post(self, **payload):
        return self.client.post('/login/', json={'username': 'email-player', **payload},
                                headers={'User-Agent': PLAYER_UA, 'X-Forwarded-For': CLIENT_IP})

    def code(self):
        return self.accounts.create_email_code(self.user['id'], 'magic')

    def test_code_signs_in_once_and_honors_safe_next(self):
        code = self.code()
        response = self.post(step='email_otp', code=code, next='https://evil.example')
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(response.get_json()['next'], '/home')
        with self.client.session_transaction() as session:
            self.assertTrue(session.get('mu_sid'))
        self.assertEqual(self.post(step='email_otp', code=code).status_code, 401)

    def test_code_does_not_bypass_authenticator(self):
        secret = self.enroll('email-player', PLAYER_PASSWORD)
        first = self.post(step='email_otp', code=self.code()).get_json()
        self.assertTrue(first['totp_required'])
        with self.client.session_transaction() as session:
            self.assertFalse(session.get('mu_sid'))
        second = self.post(step='totp', token=first['token'], code=totp_at(secret, int(time.time()) // 30))
        self.assertEqual(second.status_code, 200, second.get_json())

    def test_unknown_account_has_identical_send_response(self):
        with mock.patch('multiuser.emails.send_code_email') as send:
            known = self.post(step='send_otp')
            unknown = self.post(step='send_otp', username='unknown')
        self.assertEqual(known.get_json(), unknown.get_json())
        self.assertEqual(send.call_count, 1)

    def test_cooldown_is_shared_by_username_and_email(self):
        with mock.patch('multiuser.emails.send_code_email'):
            self.assertEqual(self.post(step='send_otp').status_code, 200)
            blocked = self.post(step='send_otp', username=self.user['email'])
        self.assertEqual(blocked.status_code, 429)
        self.assertGreater(int(blocked.headers['Retry-After']), 0)

    def test_guess_budget_and_replacement_invalidate_old_codes(self):
        old = self.code()
        fresh = self.code()
        self.assertFalse(self.accounts.consume_email_code(self.user['id'], 'magic', old))
        wrong = '000000' if fresh != '000000' else '111111'
        for _ in range(self.layer.config.email_code_attempts):
            self.accounts.consume_email_code(self.user['id'], 'magic', wrong)
        self.assertFalse(self.accounts.consume_email_code(self.user['id'], 'magic', fresh))

    def test_disabled_account_and_changed_address_cannot_spend_code(self):
        code = self.code()
        self.accounts.db.execute("UPDATE users SET status='disabled' WHERE id=?", (self.user['id'],))
        self.assertEqual(self.post(step='email_otp', code=code).status_code, 401)
        self.accounts.db.execute("UPDATE users SET status='active', email='changed@example.test' WHERE id=?", (self.user['id'],))
        self.assertEqual(self.post(step='email_otp', code=code).status_code, 401)

    def test_otp_confirms_current_email_without_applying_pending_change(self):
        self.accounts.db.execute("UPDATE users SET email_verified=0, pending_email='pending@example.test' WHERE id=?", (self.user['id'],))
        self.assertEqual(self.post(step='email_otp', code=self.code()).status_code, 200)
        user = self.accounts.get_user(self.user['id'])
        self.assertTrue(user['email_verified'])
        self.assertEqual(user['email'], self.user['email'])
        self.assertEqual(user['pending_email'], 'pending@example.test')

    def test_expired_code_is_rejected(self):
        code = self.code()
        self.accounts.db.execute("UPDATE user_email_tokens SET expires_at=1 WHERE user_id=?", (self.user['id'],))
        self.assertEqual(self.post(step='email_otp', code=code).status_code, 401)

    def test_signup_code_works_in_the_inline_login(self):
        self.accounts.db.execute("UPDATE users SET email_verified=0 WHERE id=?", (self.user['id'],))
        code = self.accounts.create_email_code(self.user['id'], 'verify')
        self.assertEqual(self.post(step='email_otp', code=code).status_code, 200)
        self.assertTrue(self.accounts.get_user(self.user['id'])['email_verified'])

    def test_pending_address_change_code_cannot_sign_in(self):
        self.accounts.db.execute("UPDATE users SET email_verified=0, pending_email='other@example.test' WHERE id=?", (self.user['id'],))
        code = self.accounts.create_email_code(self.user['id'], 'verify')
        self.assertEqual(self.post(step='email_otp', code=code).status_code, 401)

    def test_legacy_code_page_redirects_to_inline_login(self):
        response = self.admin_client.get('/verify')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/?otp=1&code=1', response.headers['Location'])
