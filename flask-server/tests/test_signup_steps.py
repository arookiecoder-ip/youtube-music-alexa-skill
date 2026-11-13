"""Email ownership must be proven before an account can be created."""
from test_signup_switch import SignupSwitchTestBase, PASSWORD


class SignupStepsTests(SignupSwitchTestBase):
    def post(self, step, **values):
        with self.client.session_transaction() as session:
            csrf = session['mu_ss_csrf']
        return self.client.post('/signup', data={'_csrf': csrf, 'step': step, **values})

    def begin(self):
        self.open_signup_form()
        response = self.post('email', email='new@example.test')
        self.assertEqual(response.status_code, 303, response.text)
        self.assertFalse(self.accounts.find_user_by_email('new@example.test'))

    def test_complete_flow_and_single_use(self):
        self.begin()
        code_page = self.client.get('/signup').text
        self.assertIn('Verification code', code_page)
        self.assertIn('data-signup-step="code"', code_page)
        self.assertIn('class="signup-actions"', code_page)
        self.assertEqual(self.post('code', code=self.newest_code()).status_code, 303)
        details_page = self.client.get('/signup').text
        self.assertIn('Repeat the password', details_page)
        self.assertIn('data-signup-step="details"', details_page)
        values = dict(username='newuser', password=PASSWORD, confirm_password=PASSWORD)
        self.assertEqual(self.post('details', **values).status_code, 200)
        user = self.accounts.get_user_by_username('newuser')
        self.assertTrue(user['email_verified'])
        self.assertEqual(self.post('details', **values).status_code, 400)

    def test_no_skipping_and_resend_cooldown(self):
        self.open_signup_form()
        self.assertEqual(self.post('details', username='skip', password=PASSWORD).status_code, 400)
        self.begin()
        self.assertEqual(self.post('details', username='skip', password=PASSWORD).status_code, 400)
        response = self.post('resend')
        self.assertEqual(response.status_code, 429)
        self.assertIn('Retry-After', response.headers)
        self.assertFalse(self.accounts.get_user_by_username('skip'))

    def test_wrong_codes_exhaust_challenge(self):
        self.begin()
        code = self.newest_code()
        wrong = '000000' if code != '000000' else '111111'
        for _ in range(self.layer.config.email_code_attempts):
            self.assertEqual(self.post('code', code=wrong).status_code, 422)
        self.assertIn(self.post('code', code=code).status_code, (422, 429))
        self.assertNotIn('Repeat the password', self.client.get('/signup').text)

    def test_existing_email_and_password_validation(self):
        self.open_signup_form()
        self.assertEqual(self.post('email', email=self.owner['email']).status_code, 409)
        self.begin()
        self.post('code', code=self.newest_code())
        self.assertEqual(self.post('details', username='newuser', password=PASSWORD,
                                   confirm_password='different').status_code, 422)
        self.assertFalse(self.accounts.get_user_by_username('newuser'))

    def test_expired_verification_cannot_create_account(self):
        self.begin()
        self.post('code', code=self.newest_code())
        self.layer.db.execute('UPDATE signup_challenges SET expires=0')
        response = self.post('details', username='expireduser', password=PASSWORD,
                             confirm_password=PASSWORD)
        self.assertEqual(response.status_code, 400)
        self.assertFalse(self.accounts.get_user_by_username('expireduser'))

    def test_restart_discards_email_proof(self):
        self.begin()
        self.post('code', code=self.newest_code())
        self.assertEqual(self.post('restart').status_code, 303)
        self.assertIn('Email address', self.client.get('/signup').text)
        self.assertEqual(self.post('details', username='restartuser', password=PASSWORD,
                                   confirm_password=PASSWORD).status_code, 400)
