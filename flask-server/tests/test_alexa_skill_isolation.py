"""Per-skill Echo isolation: a second skill cannot inherit the first's pairing."""
import hashlib
import hmac
import sys
import time
from pathlib import Path
from unittest import mock

sys.path[:0] = [str(Path(__file__).parent), str(Path(__file__).parents[1])]
from test_login_second_factor import SecondFactorDoorTestBase, PLAYER_PASSWORD  # noqa: E402

SKILL_A = 'amzn1.ask.skill.11111111-1111-1111-1111-111111111111'
SKILL_B = 'amzn1.ask.skill.22222222-2222-2222-2222-222222222222'


class SkillScopedBindingTests(SecondFactorDoorTestBase):
    def setUp(self):
        super().setUp()
        self.bind_player_door()
        self.server._ensure_db()
        self.user = self.player_user('isolation-a')
        self.other = self.player_user('isolation-b')

    def headers(self, subject='subject', device='device', skill=None):
        values = {'X-Api-Key': self.server.API_KEY,
                  'X-Alexa-User-Id': subject, 'X-Alexa-Device-Id': device}
        if skill is not None:
            values['X-Alexa-Skill-Id'] = skill
        return values

    def identity_for(self, **kwargs):
        with mock.patch.dict(self.server.app.view_functions,
                             {'armed_play': lambda: {'identity': self.server._current_identity()}}):
            return self.client.get('/armed_play/', headers=self.headers(**kwargs))

    def test_a_binding_proves_only_the_skill_that_created_it(self):
        code = self.layer.alexa_identity.issue(self.user)
        self.assertTrue(self.layer.alexa_identity.pair(code, 'subject', 'device', SKILL_A))

        allowed = self.identity_for(skill=SKILL_A)
        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(allowed.json['identity'], 'user:' + self.user['id'])

        blocked = self.identity_for(skill=SKILL_B)
        self.assertEqual(blocked.status_code, 403)
        self.assertEqual(blocked.json['error']['code'], 'link_account_required')

        # The legacy namespace is a third, separate binding too.
        legacy = self.identity_for()
        self.assertEqual(legacy.status_code, 403)

    def test_legacy_pairings_keep_working_and_stay_out_of_the_new_skill(self):
        code = self.layer.alexa_identity.issue(self.user)
        self.assertTrue(self.layer.alexa_identity.pair(code, 'subject', 'device'))

        self.assertEqual(self.identity_for().status_code, 200)
        self.assertEqual(self.identity_for(skill=SKILL_A).status_code, 403)

    def test_the_same_echo_can_be_linked_independently_per_skill(self):
        first = self.layer.alexa_identity.issue(self.user)
        second = self.layer.alexa_identity.issue(self.other)
        self.assertTrue(self.layer.alexa_identity.pair(first, 'subject', 'device', SKILL_A))
        self.assertTrue(self.layer.alexa_identity.pair(second, 'subject', 'device', SKILL_B))

        self.assertEqual(self.layer.alexa_identity.resolve('subject', 'device', SKILL_A)['id'], self.user['id'])
        self.assertEqual(self.layer.alexa_identity.resolve('subject', 'device', SKILL_B)['id'], self.other['id'])

    def test_a_pairing_code_cannot_be_replayed_into_another_skill(self):
        code = self.layer.alexa_identity.issue(self.user)
        self.assertTrue(self.layer.alexa_identity.pair(code, 'subject', 'device', SKILL_A))
        self.assertFalse(self.layer.alexa_identity.pair(code, 'subject', 'device', SKILL_B))
        self.assertFalse(self.layer.alexa_identity.pair(code, 'other', 'other-device', SKILL_B))

    def test_unknown_or_malformed_device_and_skill_are_rejected(self):
        code = self.layer.alexa_identity.issue(self.user)
        self.assertTrue(self.layer.alexa_identity.pair(code, 'subject', 'device', SKILL_A))

        response = self.identity_for(device='', skill=SKILL_A)
        self.assertEqual(response.status_code, 403)
        response = self.identity_for(skill='not-a-skill-id')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json['error']['code'], 'invalid_skill')
        self.assertFalse(self.layer.alexa_identity.pair('12345678', 's', 'd', SKILL_B))
        self.assertFalse(self.layer.alexa_identity.pair('1234567', 's', 'd', SKILL_B))

    def test_expiry_and_security_version_retire_a_code(self):
        expired = self.layer.alexa_identity.issue(self.user)
        self.accounts.db.execute('UPDATE alexa_pair_codes SET expires_at=0 WHERE user_id=?',
                                 (self.user['id'],))
        self.assertFalse(self.layer.alexa_identity.pair(expired, 's', 'd', SKILL_A))

        live = self.layer.alexa_identity.issue(self.user)
        self.accounts.db.execute('UPDATE users SET security_version = security_version + 1 WHERE id=?',
                                 (self.user['id'],))
        self.assertFalse(self.layer.alexa_identity.pair(live, 's', 'd', SKILL_A))

    def test_pairing_attempts_are_budgeted_per_echo_not_per_lambda_address(self):
        for _ in range(5):
            response = self.client.post('/api/alexa/link/',
                                        headers=self.headers(skill=SKILL_A),
                                        json={'code': '00000000'})
            self.assertEqual(response.status_code, 403)
        blocked = self.client.post('/api/alexa/link/',
                                   headers=self.headers(skill=SKILL_A),
                                   json={'code': '00000000'})
        self.assertEqual(blocked.status_code, 429)
        self.assertIn('Retry-After', blocked.headers)

        # A different Echo arriving through the same Lambda egress address is
        # not punished for the first one's guesses.
        code = self.layer.alexa_identity.issue(self.user)
        allowed = self.client.post('/api/alexa/link/',
                                   headers=self.headers(device='second-device', skill=SKILL_A),
                                   json={'code': code})
        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(self.layer.alexa_identity.resolve('subject', 'second-device', SKILL_A)['id'],
                         self.user['id'])

    def test_a_valid_pairing_still_rejects_a_disabled_account(self):
        code = self.layer.alexa_identity.issue(self.user)
        self.accounts.db.execute("UPDATE users SET status='disabled' WHERE id=?", (self.user['id'],))
        self.assertFalse(self.layer.alexa_identity.pair(code, 's', 'd', SKILL_A))
        self.assertIsNone(self.layer.alexa_identity.resolve('s', 'd', SKILL_A))

    def test_skill_namespace_parser_accepts_only_skill_ids(self):
        parse = self.layer.alexa_identity.skill_namespace
        self.assertEqual(parse(None), '')
        self.assertEqual(parse(''), '')
        self.assertEqual(parse(SKILL_A), SKILL_A)
        self.assertEqual(parse(SKILL_A.upper()), SKILL_A.lower())
        self.assertIsNone(parse('amzn1.ask.skill.not-a-uuid'))
        self.assertIsNone(parse('../../etc/passwd'))

    def _pre_v8_hash(self, value):
        # Version 7 hashed the bare subject/device/code; version 8 must keep that
        # exact formula for the legacy namespace or every existing Echo breaks.
        return hmac.new(self.layer.alexa_identity.secret, value.encode(),
                        hashlib.sha256).hexdigest()

    def test_a_legacy_row_is_still_resolved_after_the_scope_migration(self):
        # Version 8 rebuilds the table and copies every old row into the empty
        # namespace. This is what an existing installation's data looks like.
        self.accounts.db.execute(
            'INSERT INTO alexa_bindings (skill_id, subject_hash, device_hash, user_id) VALUES (?,?,?,?)',
            ('', self._pre_v8_hash('old-subject'),
             self._pre_v8_hash('old-device'), self.user['id']))
        self.assertEqual(self.layer.alexa_identity.resolve('old-subject', 'old-device')['id'],
                         self.user['id'])
        self.assertIsNone(self.layer.alexa_identity.resolve('old-subject', 'old-device', SKILL_A))

    def test_a_pairing_code_issued_before_the_upgrade_still_works(self):
        code = '42424242'
        version = self.accounts.db.scalar(
            'SELECT security_version FROM users WHERE id=?', (self.user['id'],))
        self.accounts.db.execute(
            'INSERT INTO alexa_pair_codes (code_hash, user_id, expires_at, security_version) VALUES (?,?,?,?)',
            (self._pre_v8_hash(code), self.user['id'], time.time() + 300, version))
        self.assertTrue(self.layer.alexa_identity.pair(code, 's', 'd'))
        self.assertIsNone(self.layer.alexa_identity.resolve('s', 'd', SKILL_A))
        self.assertEqual(self.layer.alexa_identity.resolve('s', 'd')['id'], self.user['id'])
