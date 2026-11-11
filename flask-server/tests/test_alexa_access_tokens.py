"""Private skill access, live revocation and independently verified email."""
import hashlib
import time
import sys
from pathlib import Path
sys.path[:0] = [str(Path(__file__).parent), str(Path(__file__).parents[1])]
from unittest import mock
from test_login_second_factor import SecondFactorDoorTestBase
from multiuser.alexa_access import AccessDenied

class AlexaAccessTokenTests(SecondFactorDoorTestBase):
    def setUp(self):
        super().setUp()
        self.bind_player_door()
        self.user = self.player_user('private-skill')
        self.access = self.layer.alexa_access
        self.access.set_enabled(self.user['id'], True)
        self.token = self.access.rotate(self.user['id'])
        self.headers = {'Authorization': 'Bearer ' + self.token,
            'X-MusicBox-Email': self.user['email'], 'X-Alexa-User-Id': 'subject',
            'X-Alexa-Device-Id': 'device', 'X-Alexa-Profile-Token': 'amazon-proof',
            'X-Alexa-Api-Endpoint': 'https://api.amazonalexa.com'}
        self.amazon = mock.patch('multiuser.alexa_access.requests.get')
        self.lookup = self.amazon.start()
        self.addCleanup(self.amazon.stop)
        self.lookup.return_value.status_code = 200
        self.lookup.return_value.json.return_value = self.user['email']

    def identity(self, headers=None):
        return self.client.get('/api/alexa/identity/', headers=headers or self.headers)

    def test_scoped_credential_resolves_only_its_user_and_checks_amazon(self):
        result = self.identity()
        self.assertEqual(result.status_code, 200)
        self.assertEqual(len(result.json['binding']), 64)
        kwargs = self.lookup.call_args.kwargs
        self.assertFalse(kwargs['allow_redirects'])
        self.assertEqual(kwargs['headers']['Authorization'], 'Bearer amazon-proof')
        self.assertNotIn(self.token, str(self.access.metadata(self.user['id'])))
        row = self.layer.db.fetchone('SELECT * FROM alexa_access_tokens WHERE id=?', (self.token.split('.')[0],))
        self.assertEqual(row['token_hash'], hashlib.sha256(self.token.encode()).hexdigest())

    def test_rotation_revokes_old_requests_and_existing_audio_urls(self):
        user, credential = self.access.authenticate(self.headers)
        cap = self.layer.alexa_identity.stream_token(user['id'], 'AAAAAAAAAAA', credential)
        self.assertIsNotNone(self.layer.alexa_identity.stream_user(cap, 'AAAAAAAAAAA'))
        self.access.rotate(self.user['id'])
        self.assertEqual(self.identity().status_code, 403)
        self.assertIsNone(self.layer.alexa_identity.stream_user(cap, 'AAAAAAAAAAA'))

    def test_off_then_on_never_resurrects_a_token(self):
        self.access.set_enabled(self.user['id'], False)
        self.assertEqual(self.identity().status_code, 403)
        self.access.set_enabled(self.user['id'], True)
        self.assertEqual(self.identity().status_code, 403)
        self.headers['Authorization'] = 'Bearer ' + self.access.rotate(self.user['id'])
        self.assertEqual(self.identity().status_code, 200)

    def test_client_email_and_real_amazon_email_must_both_match(self):
        self.lookup.return_value.json.return_value = 'someone-else@example.test'
        self.assertEqual(self.identity().json['error']['code'], 'amazon_email_mismatch')
        self.lookup.return_value.json.return_value = self.user['email']
        self.headers['X-MusicBox-Email'] = 'someone-else@example.test'
        self.assertEqual(self.identity().status_code, 403)

    def test_ssrf_redirect_permission_denial_and_missing_identity_fail_closed(self):
        for endpoint in ('https://api.amazonalexa.com.evil.test', 'http://127.0.0.1', 'https://api.amazonalexa.com/private'):
            self.headers['X-Alexa-Api-Endpoint'] = endpoint
            self.lookup.reset_mock()
            self.assertEqual(self.identity().status_code, 403)
            self.lookup.assert_not_called()
        self.headers['X-Alexa-Api-Endpoint'] = 'https://api.amazonalexa.com'
        for code in (302, 401, 403, 429, 500):
            self.lookup.return_value.status_code = code
            self.assertEqual(self.identity().status_code, 403)
        self.lookup.return_value.status_code = 200
        self.headers.pop('X-Alexa-Device-Id')
        self.assertEqual(self.identity().status_code, 403)

    def test_token_cannot_open_admin_account_cookies_or_unreviewed_endpoints(self):
        for path in ('/admin/', '/api/account', '/api/youtube/browser-session/status', '/alexa/form_login/', '/api/app/output/'):
            self.assertEqual(self.client.get(path, headers=self.headers).status_code, 403, path)

    def test_restrictions_email_password_expiry_and_revoke_retire_credentials(self):
        for sql in ("UPDATE users SET status='disabled' WHERE id=?",
                    "UPDATE users SET must_change_password=1 WHERE id=?",
                    "UPDATE users SET email_verified=0 WHERE id=?"):
            with self.subTest(sql=sql):
                self.layer.db.execute(sql, (self.user['id'],))
                self.assertEqual(self.identity().status_code, 403)
                self.layer.db.execute("UPDATE users SET status='active',must_change_password=0,email_verified=1 WHERE id=?", (self.user['id'],))
                self.headers['Authorization'] = 'Bearer ' + self.access.rotate(self.user['id'])
        self.access.revoke(self.user['id'])
        self.assertEqual(self.identity().status_code, 403)
        self.headers['Authorization'] = 'Bearer ' + self.access.rotate(self.user['id'])
        self.layer.db.execute('UPDATE alexa_access_tokens SET expires_at=? WHERE user_id=?', (time.time()-1,self.user['id']))
        self.assertEqual(self.identity().status_code, 403)

    def test_revocation_during_amazon_call_is_rechecked(self):
        def revoke(*args, **kwargs):
            self.access.revoke(self.user['id'])
            return mock.Mock(status_code=200, json=lambda: self.user['email'])
        self.lookup.side_effect = revoke
        self.assertEqual(self.identity().status_code, 403)

    def test_new_accounts_default_off_and_cannot_mint_tokens(self):
        other = self.player_user('default-off')
        self.assertFalse(self.access.enabled(other['id']))
        with self.assertRaises(ValueError): self.access.rotate(other['id'])

    def test_admin_toggle_and_one_time_token_reveal_are_csrf_protected(self):
        self.assertEqual(self.admin_step1('owner', self.admin_password).status_code, 302)
        path = '/admin/users/' + self.user['id'] + '/alexa/'
        self.assertEqual(self.admin_client.post(path, data={'action': 'generate'}).status_code, 400)
        csrf = self.admin_csrf()
        response = self.admin_client.post(path, data={'_csrf': csrf, 'action': 'generate'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        import re
        raw = re.search(r'mbx_[0-9a-f]{24}\.[A-Za-z0-9_-]{43}', response.text).group()
        with self.admin_client.session_transaction() as session:
            self.assertNotIn(raw, str(dict(session)))
        response = self.admin_client.get('/admin/users/' + self.user['id'] + '/')
        self.assertNotIn(raw, response.text)
        response = self.admin_client.post(path, data={'_csrf': csrf, 'action': 'access'})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(self.access.enabled(self.user['id']))
        self.assertEqual(self.identity().status_code, 403)

    def test_disabled_account_payload_and_html_omit_amazon(self):
        self.access.set_enabled(self.user['id'], False)
        sid = self.accounts.create_session(self.accounts.get_user(self.user['id']), ip='127.0.0.1', user_agent='pytest')
        web = self.layer_app.test_client()
        with web.session_transaction() as session:
            session['mu_sid'] = sid
        result = web.get('/api/account')
        self.assertEqual(result.status_code, 200)
        self.assertIs(result.json['alexa_access_enabled'], False)
        self.assertNotIn('amazon', result.json['provider_map'])
        self.assertNotIn('id="service-amazon"', web.get('/account').text)

    def test_disabled_access_blocks_direct_amazon_routes_for_stale_clients(self):
        sid = self.accounts.create_session(self.accounts.get_user(self.user['id']), ip='127.0.0.1', user_agent='pytest')
        with self.client.session_transaction() as session:
            session['mu_sid'] = sid
        self.access.set_enabled(self.user['id'], False)
        for path in ('/accounts/amazon', '/alexa/status/', '/alexa/devices/', '/alexa/form_login/', '/alexa/form_login/step/', '/alexa/proxy_login/'):
            result = self.client.post(path, json={}) if 'login' in path else self.client.get(path)
            self.assertEqual(result.status_code, 403, path)

    def test_stale_or_non_ascii_binding_is_rejected_without_an_internal_error(self):
        for binding in ('old', 'é', 'b' * 64):
            self.headers['X-Alexa-Binding'] = binding
            self.assertEqual(self.identity().status_code, 403)

    def test_all_documented_amazon_regions_are_supported(self):
        from multiuser.alexa_access import AMAZON_ENDPOINTS
        for endpoint in AMAZON_ENDPOINTS:
            self.headers['X-Alexa-Api-Endpoint'] = endpoint
            self.assertEqual(self.identity().status_code, 200, endpoint)

    def test_additional_owner_never_inherits_bootstrap_owner_playback_or_cookies(self):
        second = self.accounts.create_user(self.owner, 'other-owner', 'Another!Passw0rd',
            email='other-owner@example.test', role='owner', must_change_password=False, email_verified=True)
        with mock.patch.object(self.server, '_owner_row_read', [0.0, '']):
            self.assertEqual(self.server._player_identity_for_user_id(self.owner['id'], 'owner'), 'owner')
            self.assertEqual(self.server._player_identity_for_user_id(second['id'], 'owner'), 'user:' + second['id'])
        self.layer.db.execute("UPDATE users SET status='disabled' WHERE id=?", (self.owner['id'],))
        with mock.patch.object(self.server, '_owner_row_read', [0.0, '']):
            self.assertEqual(self.server._player_identity_for_user_id(second['id'], 'owner'), 'user:' + second['id'])

    def test_account_email_change_and_password_change_invalidate_token_and_stream(self):
        for field, value in (('email', 'changed@example.test'), ('password_hash', 'changed-hash')):
            token = self.access.rotate(self.user['id'])
            headers = dict(self.headers, Authorization='Bearer ' + token)
            self.lookup.return_value.json.return_value = headers['X-MusicBox-Email']
            user, credential = self.access.authenticate(headers)
            cap = self.layer.alexa_identity.stream_token(user['id'], 'AAAAAAAAAAA', credential)
            self.layer.db.execute('UPDATE users SET ' + field + '=? WHERE id=?', (value, self.user['id']))
            self.assertEqual(self.identity(headers).status_code, 403)
            self.assertIsNone(self.layer.alexa_identity.stream_user(cap, 'AAAAAAAAAAA'))
            self.layer.db.execute('UPDATE users SET email=? WHERE id=?', (self.user['email'], self.user['id']))

    def test_amazon_timeouts_and_invalid_profile_json_fail_closed(self):
        import requests
        self.lookup.side_effect = requests.Timeout('offline')
        self.assertEqual(self.identity().json['error']['code'], 'amazon_profile_unavailable')
        self.lookup.side_effect = None
        self.lookup.return_value.json.side_effect = ValueError('invalid JSON')
        self.assertEqual(self.identity().json['error']['code'], 'amazon_profile_unavailable')
