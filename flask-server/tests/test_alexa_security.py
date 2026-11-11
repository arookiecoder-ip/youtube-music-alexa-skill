"""Echo reassignment, capability revocation, and complete-identity boundaries."""
import asyncio
import sys
import time
from pathlib import Path
from unittest import mock

from itsdangerous import URLSafeTimedSerializer

sys.path[:0] = [str(Path(__file__).parent), str(Path(__file__).parents[1])]
from test_login_second_factor import SecondFactorDoorTestBase

SKILL_A = 'amzn1.ask.skill.11111111-1111-1111-1111-111111111111'


class AlexaSecurityTests(SecondFactorDoorTestBase):
    def setUp(self):
        super().setUp()
        self.bind_player_door()
        self.server._ensure_db()
        self.user = self.player_user('security-a')
        self.other = self.player_user('security-b')
        self.layer.alexa_access.set_enabled(self.user['id'], True)
        self.layer.alexa_access.set_enabled(self.other['id'], True)

    def headers(self, skill=SKILL_A):
        return {'X-Api-Key': self.server.API_KEY, 'X-Alexa-User-Id': 'subject',
                'X-Alexa-Device-Id': 'device', 'X-Alexa-Skill-Id': skill}

    def bind(self, user=None, device='device'):
        identity = self.layer.alexa_identity
        self.assertTrue(identity.pair(identity.issue(user or self.user), 'subject', device, SKILL_A))
        return identity.binding('subject', device, SKILL_A)[1]

    def test_missing_identity_never_falls_back_to_owner(self):
        for missing in ('X-Alexa-User-Id', 'X-Alexa-Device-Id'):
            headers = self.headers(skill=SKILL_A)
            headers.pop(missing)
            with mock.patch.dict(self.server.app.view_functions, {'get_stream': mock.Mock()}) as views:
                response = self.client.get('/get_stream/?video_id=AAAAAAAAAAA', headers=headers)
                self.assertEqual(response.status_code, 403)
                views['get_stream'].assert_not_called()

    def test_alexa_headers_cannot_use_a_browser_session_instead_of_skill_key(self):
        headers = self.headers(skill=SKILL_A)
        headers.pop('X-Api-Key')
        self.assertEqual(self.client.get('/get_stream/', headers=headers).status_code, 401)

    def test_identity_endpoint_requires_a_paired_echo(self):
        self.assertEqual(self.client.get('/api/alexa/identity/', headers=self.headers(skill=SKILL_A)).status_code, 403)
        binding = self.bind()
        response = self.client.get('/api/alexa/identity/', headers=self.headers(skill=SKILL_A))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, {'binding': binding['version']})
        self.assertIn('no-store', response.headers['Cache-Control'])
        self.assertEqual(self.client.get('/api/alexa/identity/', headers={'X-Api-Key': self.server.API_KEY}).status_code, 403)

    def test_v8_migration_preserves_bindings_and_is_repeatable(self):
        self.bind()
        db = self.layer.db
        with db.connect() as conn:
            conn.execute('ALTER TABLE alexa_bindings DROP COLUMN generation')
            conn.execute('DELETE FROM multiuser_schema_migrations WHERE version=9')
        db._initialized = False
        db.initialize()
        user, binding = self.layer.alexa_identity.binding('subject', 'device', SKILL_A)
        self.assertEqual(user['id'], self.user['id'])
        self.assertEqual(len(binding['generation']), 32)
        db._initialized = False
        db.initialize()
        self.assertEqual(self.layer.alexa_identity.binding('subject', 'device', SKILL_A)[1]['version'], binding['version'])

    def test_repair_revokes_old_stream_without_revoking_other_devices(self):
        identity = self.layer.alexa_identity
        a = self.bind()
        other = self.bind(device='other-device')
        cap = identity.stream_token(self.user['id'], 'AAAAAAAAAAA', binding=a)
        unaffected = identity.stream_token(self.user['id'], 'AAAAAAAAAAA', binding=other)
        b = self.bind(self.other)
        self.assertNotEqual(a['version'], b['version'])
        self.assertIsNone(identity.stream_user(cap, 'AAAAAAAAAAA'))
        self.assertEqual(identity.stream_user(unaffected, 'AAAAAAAAAAA')['id'], self.user['id'])
        self.assertEqual(identity.resolve('subject', 'device', SKILL_A)['id'], self.other['id'])

    def test_same_account_repair_also_rotates_generation(self):
        a = self.bind()
        cap = self.layer.alexa_identity.stream_token(self.user['id'], 'AAAAAAAAAAA', binding=a)
        b = self.bind()
        self.assertNotEqual(a['version'], b['version'])
        self.assertIsNone(self.layer.alexa_identity.stream_user(cap, 'AAAAAAAAAAA'))

    def test_stale_inflight_binding_cannot_mutate_new_account(self):
        previous = self.bind()
        current = self.bind(self.other)
        headers = self.headers(skill=SKILL_A)
        headers['X-Alexa-Binding'] = previous['version']
        with mock.patch.dict(self.server.app.view_functions, {'armed_play': mock.Mock()}) as views:
            self.assertEqual(self.client.get('/armed_play/', headers=headers).status_code, 403)
            views['armed_play'].assert_not_called()
        headers['X-Alexa-Binding'] = current['version']
        self.assertEqual(self.client.get('/api/alexa/identity/', headers=headers).status_code, 200)

    def test_password_revocation_changes_state_generation_and_stream_access(self):
        a = self.bind()
        identity = self.layer.alexa_identity
        cap = identity.stream_token(self.user['id'], 'AAAAAAAAAAA', binding=a)
        self.accounts.invalidate_security_proofs(self.user['id'])
        b = identity.binding('subject', 'device', SKILL_A)[1]
        self.assertNotEqual(a['version'], b['version'])
        self.assertIsNone(identity.stream_user(cap, 'AAAAAAAAAAA'))

    def test_request_authorized_before_revocation_cannot_mint_a_new_version(self):
        a = self.bind()
        identity = self.layer.alexa_identity
        self.accounts.invalidate_security_proofs(self.user['id'])
        late_cap = identity.stream_token(self.user['id'], 'AAAAAAAAAAA', binding=a)
        self.assertIsNone(identity.stream_user(late_cap, 'AAAAAAAAAAA'))

    def test_removed_binding_revokes_capability(self):
        binding = self.bind()
        identity = self.layer.alexa_identity
        cap = identity.stream_token(self.user['id'], 'AAAAAAAAAAA', binding=binding)
        self.layer.db.execute('DELETE FROM alexa_bindings WHERE user_id=?', (self.user['id'],))
        self.assertIsNone(identity.stream_user(cap, 'AAAAAAAAAAA'))

    def test_account_unlink_revokes_echo_access_without_logging_out_the_browser(self):
        import re
        web = self.layer_app.test_client()
        with web.session_transaction() as state:
            state['mu_sid'] = self.accounts.create_session(self.user)
        page = web.get('/account')
        csrf = re.search(rb'data-csrf="([^"]+)"', page.data).group(1).decode()
        binding = self.bind()
        identity = self.layer.alexa_identity
        cap = identity.stream_token(self.user['id'], 'AAAAAAAAAAA', binding=binding)
        response = web.post('/api/account/alexa/unlink', json={}, headers={'X-CSRF-Token': csrf})
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(identity.resolve('subject', 'device', SKILL_A))
        self.assertIsNone(identity.stream_user(cap, 'AAAAAAAAAAA'))
        self.assertEqual(web.get('/api/account').status_code, 200)

    def test_account_cannot_generate_new_pairing_codes(self):
        import re
        web = self.layer_app.test_client()
        with web.session_transaction() as state:
            state['mu_sid'] = self.accounts.create_session(self.user)
        page = web.get('/account')
        self.assertNotIn(b'Generate pairing code', page.data)
        csrf = re.search(rb'data-csrf="([^"]+)"', page.data).group(1).decode()
        response = web.post('/api/account/alexa/link-code', json={}, headers={'X-CSRF-Token': csrf})
        self.assertEqual(response.status_code, 410)
        self.assertEqual(self.layer.db.scalar('SELECT count(*) FROM alexa_pair_codes'), 0)

    def test_bound_capability_rejects_wrong_video_and_expiry(self):
        identity = self.layer.alexa_identity
        binding = self.bind()
        cap = identity.stream_token(self.user['id'], 'AAAAAAAAAAA', binding=binding)
        self.assertIsNone(identity.stream_user(cap, 'BBBBBBBBBBB'))
        with mock.patch('itsdangerous.timed.TimestampSigner.get_timestamp', return_value=int(time.time()) + 7201):
            self.assertIsNone(identity.stream_user(cap, 'AAAAAAAAAAA'))

    def test_legacy_unbound_capabilities_and_malformed_payloads_are_rejected(self):
        identity = self.layer.alexa_identity
        old = URLSafeTimedSerializer(str(identity.secret, 'utf-8'), salt='musicbox-echo-stream-v1')
        cap = old.dumps({'user_id': self.user['id'], 'video': 'AAAAAAAAAAA',
                         'version': self.accounts.security_version(self.user['id'])})
        self.assertIsNone(identity.stream_user(cap, 'AAAAAAAAAAA'))
        self.assertIsNone(identity.stream_user(identity.signer.dumps(['invalid']), 'AAAAAAAAAAA'))

    def test_real_stream_builder_issues_a_binding_scoped_capability(self):
        self.bind()
        identity = self.layer.alexa_identity
        headers = self.headers(skill=SKILL_A)
        def build():
            return asyncio.run(self.server.Supporting.get_stream('AAAAAAAAAAA'))
        with mock.patch.dict(self.server.app.view_functions, {'get_stream': build}), \
                mock.patch.object(self.server, '_ensure_audio_ready_for_play'), \
                mock.patch.object(self.server, 'PUBLIC_BASE_URL', 'https://music.example'):
            response = self.client.get('/get_stream/?video_id=AAAAAAAAAAA', headers=headers)
        self.assertEqual(response.status_code, 200)
        from urllib.parse import urlsplit, parse_qs
        cap = parse_qs(urlsplit(response.json['audio_url']).query)['cap'][0]
        self.assertEqual(identity.stream_user(cap, 'AAAAAAAAAAA')['id'], self.user['id'])
        self.bind(self.other)
        self.assertIsNone(identity.stream_user(cap, 'AAAAAAAAAAA'))
