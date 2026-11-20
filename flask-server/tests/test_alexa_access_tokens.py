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
        self.amazon_email = 'linked-amazon@example.test'
        self.access.link_amazon(self.user['id'], self.amazon_email, 'customer-B')
        self.token = self.access.rotate(self.user['id'])
        self.headers = {'Authorization': 'Bearer ' + self.token,
            'X-MusicBox-Email': self.user['email'], 'X-Alexa-User-Id': 'subject',
            'X-Alexa-Device-Id': 'device', 'X-Alexa-Profile-Token': 'amazon-proof',
            'X-Alexa-Api-Endpoint': 'https://api.amazonalexa.com'}
        self.amazon = mock.patch('multiuser.alexa_access.AlexaAccess._profile_get')
        self.lookup = self.amazon.start()
        self.addCleanup(self.amazon.stop)
        self.lookup.return_value.status_code = 200
        self.lookup.return_value.json.return_value = self.amazon_email

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
        self.lookup.return_value.json.return_value = self.amazon_email
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
            return mock.Mock(status_code=200, json=lambda: self.amazon_email)
        self.lookup.side_effect = revoke
        self.assertEqual(self.identity().status_code, 403)

    def test_new_accounts_default_off_and_cannot_mint_tokens(self):
        other = self.player_user('default-off')
        self.assertFalse(self.access.enabled(other['id']))
        with self.assertRaises(ValueError): self.access.rotate(other['id'])

    def test_admin_private_config_download_is_csrf_protected_and_not_rendered(self):
        self.assertEqual(self.admin_step1('owner', self.admin_password).status_code, 302)
        path = '/admin/users/' + self.user['id'] + '/alexa/'
        self.assertEqual(self.admin_client.post(path, data={'action': 'generate'}).status_code, 400)
        csrf = self.admin_csrf()
        response = self.admin_client.post(path, data={'_csrf': csrf, 'action': 'generate'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        self.assertIn('attachment', response.headers['Content-Disposition'])
        self.assertIn('MUSICBOX_ACCOUNT_ID='+self.user['id'], response.text)
        self.assertNotIn('MUSICBOX_EMAIL=', response.text)
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

    def test_metadata_exposes_only_the_current_unexpired_credential(self):
        self.access.rotate(self.user['id'])
        raw=self.access.rotate(self.user['id'])
        rows=self.access.metadata(self.user['id'])
        self.assertEqual([r['id'] for r in rows],[raw.split('.')[0]])
        self.access.revoke(self.user['id'])
        self.assertEqual(self.access.metadata(self.user['id']),[])

    def test_database_prevents_two_unrevoked_tokens_for_one_account(self):
        import sqlite3
        with self.assertRaises(sqlite3.IntegrityError):
            self.layer.db.execute("INSERT INTO alexa_access_tokens (id,user_id,token_hash,email,security_version,created_at,expires_at) SELECT 'mbx_duplicate',user_id,token_hash,email,security_version,created_at+1,expires_at FROM alexa_access_tokens WHERE id=?", (self.token.split('.')[0],))
        self.assertEqual(self.identity().status_code, 200)

    def test_upgrade_deduplicates_tokens_and_keeps_the_newest_credential(self):
        from multiuser.alexa_access import AccessDenied
        old_id = self.token.split('.')[0]
        with self.layer.db.connect() as conn:
            conn.execute('DROP INDEX idx_alexa_one_active_token')
            conn.execute('DELETE FROM multiuser_schema_migrations WHERE version=15')
            conn.execute("INSERT INTO alexa_access_tokens (id,user_id,token_hash,email,security_version,created_at,expires_at) SELECT 'mbx_newer',user_id,token_hash,email,security_version,created_at+1,expires_at FROM alexa_access_tokens WHERE id=?", (old_id,))
        self.layer.db._initialized=False
        self.layer.db.initialize()
        with self.assertRaises(AccessDenied): self.access.current(old_id)
        self.assertEqual(self.access.current('mbx_newer')[0]['id'], self.user['id'])
        self.assertEqual(self.layer.db.scalar('SELECT COUNT(*) FROM alexa_access_tokens WHERE user_id=? AND revoked_at=0',(self.user['id'],)),1)

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
        # A newly booted CI/server host can still be inside its first minute.
        # An uninitialized cache must not be mistaken for a cached empty owner.
        with mock.patch.object(self.server, '_owner_row_read', [0.0, '']), \
                mock.patch.object(self.server.time, 'monotonic', return_value=30.0):
            self.assertEqual(self.server._player_identity_for_user_id(self.owner['id'], 'owner'), 'owner')
            self.assertEqual(self.server._player_identity_for_user_id(second['id'], 'owner'), 'user:' + second['id'])
        self.layer.db.execute("UPDATE users SET status='disabled' WHERE id=?", (self.owner['id'],))
        with mock.patch.object(self.server, '_owner_row_read', [0.0, '']):
            self.assertEqual(self.server._player_identity_for_user_id(second['id'], 'owner'), 'user:' + second['id'])

    def test_account_email_change_and_password_change_preserve_token_and_stream_until_revoked(self):
        for field, value in (('email', 'changed@example.test'), ('password_hash', 'changed-hash')):
            token = self.access.rotate(self.user['id'])
            headers = dict(self.headers, Authorization='Bearer ' + token)
            self.lookup.return_value.json.return_value = self.amazon_email
            user, credential = self.access.authenticate(headers)
            cap = self.layer.alexa_identity.stream_token(user['id'], 'AAAAAAAAAAA', credential)
            self.layer.db.execute('UPDATE users SET ' + field + '=? WHERE id=?', (value, self.user['id']))
            self.assertEqual(self.identity(headers).status_code, 200)
            self.assertIsNotNone(self.layer.alexa_identity.stream_user(cap, 'AAAAAAAAAAA'))
            self.layer.db.execute('UPDATE users SET email=? WHERE id=?', (self.user['email'], self.user['id']))

    def test_amazon_timeouts_and_invalid_profile_json_fail_closed(self):
        import requests
        self.lookup.side_effect = requests.Timeout('offline')
        self.assertEqual(self.identity().json['error']['code'], 'amazon_profile_unavailable')
        self.lookup.side_effect = None
        self.lookup.return_value.json.side_effect = ValueError('invalid JSON')
        self.assertEqual(self.identity().json['error']['code'], 'amazon_profile_unavailable')

    def test_unlinked_or_unverified_amazon_identity_cannot_authorize_voice(self):
        self.access.unlink_amazon(self.user['id'])
        self.assertEqual(self.identity().json['error']['code'], 'amazon_link_required')
        self.lookup.assert_not_called()
        self.accounts.set_provider_credentials(None, self.user['id'], 'amazon',
            {'email': self.amazon_email}, {'account_email': self.amazon_email})
        self.assertEqual(self.identity().json['error']['code'], 'amazon_link_required')

    def test_relink_changes_binding_and_retires_previous_audio_capability(self):
        user, credential = self.access.authenticate(self.headers)
        cap = self.layer.alexa_identity.stream_token(user['id'], 'AAAAAAAAAAA', credential)
        self.access.unlink_amazon(user['id'])
        self.assertIsNone(self.layer.alexa_identity.stream_user(cap, 'AAAAAAAAAAA'))
        self.access.link_amazon(user['id'], self.amazon_email, 'customer-B')
        _, replacement = self.access.authenticate(self.headers)
        self.assertNotEqual(credential['version'], replacement['version'])
        self.assertIsNone(self.layer.alexa_identity.stream_user(cap, 'AAAAAAAAAAA'))
        # A normal status revalidation of the same session does not cut music.
        self.access.link_amazon(user['id'], self.amazon_email.upper(), 'customer-B')
        _, same = self.access.authenticate(self.headers)
        self.assertEqual(replacement['version'], same['version'])

    def test_account_replacement_during_profile_call_is_rejected(self):
        def replace(*args, **kwargs):
            self.access.link_amazon(self.user['id'], 'new-amazon@example.test', 'customer-C')
            return mock.Mock(status_code=200, json=lambda: self.amazon_email)
        self.lookup.side_effect = replace
        self.assertEqual(self.identity().json['error']['code'], 'amazon_link_changed')

    def test_linked_amazon_identity_is_isolated_between_musicbox_users(self):
        other = self.player_user('amazon-isolated')
        self.access.set_enabled(other['id'], True)
        self.access.link_amazon(other['id'], 'other-amazon@example.test', 'customer-other')
        self.assertEqual(self.access.linked_amazon(self.user['id'])['email'], self.amazon_email)
        self.lookup.return_value.json.return_value = 'other-amazon@example.test'
        self.assertEqual(self.identity().json['error']['code'], 'amazon_email_mismatch')

    def test_disabled_user_cannot_publish_new_amazon_identity(self):
        self.access.set_enabled(self.user['id'], False)
        with self.assertRaises(AccessDenied):
            self.access.link_amazon(self.user['id'], 'new@example.test', 'customer-C')
        self.assertEqual(self.access.linked_amazon(self.user['id'])['email'], self.amazon_email)


    def test_amazon_identity_denial_stops_only_its_own_pending_playback(self):
        from datetime import datetime, timezone
        server = self.server
        token = server._PLAYER_IDENTITY.set('user:'+self.user['id'])
        try:
            server._now_playing.update(video_id='AAAAAAAAAAA', queue=[{'video_id':'AAAAAAAAAAA'}],
                queue_index=0, playing=False, playback_processing=True, playback_confirmed=False,
                alexa_intent_at=time.time()-2, _alexa_auth_blocked=False)
        finally:
            server._PLAYER_IDENTITY.reset(token)
        self.lookup.return_value.json.return_value='different@example.test'
        headers={**self.headers,'X-Alexa-Request-Timestamp':datetime.now(timezone.utc).isoformat(),
                 'X-Alexa-Request-Type':'IntentRequest'}
        with mock.patch.object(server,'_notify_sse'):
            response=self.identity(headers)
        self.assertEqual(response.status_code,403)
        self.assertEqual(response.json['error']['code'],'amazon_email_mismatch')
        token=server._PLAYER_IDENTITY.set('user:'+self.user['id'])
        try:
            self.assertFalse(server._now_playing['playback_processing'])
            self.assertTrue(server._now_playing['_alexa_auth_blocked'])
            self.assertEqual(server._now_playing['playback_error']['code'],'amazon_email_mismatch')
            self.assertEqual(server._now_playing['video_id'],'AAAAAAAAAAA')
        finally:
            server._PLAYER_IDENTITY.reset(token)

    def test_forged_and_revoked_tokens_cannot_report_playback_failures(self):
        self.assertIsNotNone(self.access.failure_owner(self.headers))
        forged={**self.headers,'Authorization':self.headers['Authorization']+'x'}
        self.assertIsNone(self.access.failure_owner(forged))
        self.access.revoke(self.user['id'])
        self.assertIsNone(self.access.failure_owner(self.headers))


    def test_stale_identity_denial_returns_fence_without_stopping_new_intent(self):
        from datetime import datetime, timezone
        server=self.server
        identity='user:'+self.user['id']
        player=server._player_registry.state_for(identity)
        player.update(video_id='AAAAAAAAAAA',playing=False,playback_processing=True,
                      alexa_intent_at=time.time(),_alexa_auth_blocked=False)
        self.lookup.return_value.json.return_value='different@example.test'
        headers={**self.headers,'X-Alexa-Request-Timestamp':datetime.fromtimestamp(time.time()-20,timezone.utc).isoformat(),
                 'X-Alexa-Request-Type':'IntentRequest'}
        with mock.patch.object(server,'_notify_sse') as notify:
            response=self.identity(headers)
        self.assertEqual(response.status_code,403)
        self.assertEqual(response.json['error']['code'],'stale_alexa_request')
        self.assertTrue(player['playback_processing'])
        self.assertFalse(player['_alexa_auth_blocked'])
        notify.assert_not_called()

    def test_identity_can_bundle_account_scoped_resume_without_consuming_it(self):
        from urllib.parse import urlsplit, parse_qs
        server=self.server;video='AAAAAAAAAAA'
        identity=server._player_identity_for_user_id(self.user['id'])
        token=server._PLAYER_IDENTITY.set(identity)
        try:
            server._now_playing.update(video_id=video,alexa_intent_at=42)
            server._arm_play('echo',video,17000,kind='resume')
            with mock.patch.object(server,'PUBLIC_BASE_URL','https://music.example'), \
                    mock.patch.object(server,'_ensure_audio_ready_for_play'):
                result=self.client.get('/api/alexa/identity/?include_resume=1',headers=self.headers)
            self.assertEqual(result.status_code,200)
            self.assertEqual(result.json['resume']['offset_ms'],17000)
            self.assertIn('no-store',result.headers['Cache-Control'])
            cap=parse_qs(urlsplit(result.json['resume']['resume_stream']['audio_url']).query)['cap'][0]
            self.assertEqual(self.layer.alexa_identity.stream_user(cap,video)['id'],self.user['id'])
            self.assertEqual(server._peek_armed_play('echo')[0],video)
            self.assertEqual(server._now_playing['_alexa_verified_intent_at'],42)
        finally:
            server._PLAYER_IDENTITY.reset(token)

    def test_identity_resume_bundle_does_not_mint_stream_for_play_or_missing_arm(self):
        server=self.server;token=server._PLAYER_IDENTITY.set(server._player_identity_for_user_id(self.user['id']))
        try:
            with mock.patch.object(server.Supporting,'get_stream',new=mock.AsyncMock()) as stream:
                empty=self.client.get('/api/alexa/identity/?include_resume=1',headers=self.headers)
                self.assertEqual(empty.json['resume'],{'video_id':None})
                server._arm_play('echo','AAAAAAAAAAA',kind='play')
                fresh=self.client.get('/api/alexa/identity/?include_resume=1',headers=self.headers)
                self.assertEqual(fresh.json['resume']['kind'],'play')
                self.assertNotIn('resume_stream',fresh.json['resume'])
                self.assertIsNotNone(server._peek_armed_play('echo'))
            stream.assert_not_awaited()
        finally:
            server._PLAYER_IDENTITY.reset(token)

    def test_revoked_identity_cannot_read_or_mint_a_resume_bundle(self):
        self.access.revoke(self.user['id'])
        with mock.patch.object(self.server,'_peek_armed_payload') as peek:
            result=self.client.get('/api/alexa/identity/?include_resume=1',headers=self.headers)
        self.assertEqual(result.status_code,403);peek.assert_not_called()

    def test_fresh_play_identity_bundles_and_consumes_only_the_accounts_arm(self):
        from urllib.parse import urlsplit,parse_qs
        server=self.server;identity=server._player_identity_for_user_id(self.user['id'])
        slot=server._PLAYER_IDENTITY.set(identity)
        try:
            video='AAAAAAAAAAA'
            server._now_playing.update(video_id=video,queue=[{'video_id':video,'title':'Own song','artist':'Artist'}],queue_index=0)
            server._arm_play('echo',video,12000)
            headers={**self.headers,'X-Alexa-Request-Type':'IntentRequest','X-Alexa-Intent-Name':'PlaySongIntent'}
            with mock.patch.object(server,'PUBLIC_BASE_URL','https://music.example'),mock.patch.object(server,'_ensure_audio_ready_for_play'):
                result=self.client.get('/api/alexa/identity/?include_play=1',headers=headers)
            self.assertEqual(result.status_code,200)
            play=result.json['play'];self.assertEqual(play['video_id'],video)
            self.assertEqual(play['offset_ms'],12000)
            self.assertEqual(play['startup']['song_info']['metadata']['title'],'Own song')
            cap=parse_qs(urlsplit(play['startup']['song_info']['stream']['audio_url']).query)['cap'][0]
            self.assertEqual(self.layer.alexa_identity.stream_user(cap,video)['id'],self.user['id'])
            self.assertIsNone(server._peek_armed_play('echo'))
            self.assertEqual(self.client.get('/api/alexa/identity/?include_play=1',headers=headers).json['play'],{'video_id':None})
        finally:server._PLAYER_IDENTITY.reset(slot)

    def test_fresh_play_opt_in_cannot_consume_an_arm_for_another_intent(self):
        server=self.server;slot=server._PLAYER_IDENTITY.set(server._player_identity_for_user_id(self.user['id']))
        try:
            server._arm_play('echo','AAAAAAAAAAA')
            for kind,name in [('IntentRequest','AMAZON.PauseIntent'),('AudioPlayer.PlaybackStarted','PlaySongIntent')]:
                result=self.client.get('/api/alexa/identity/?include_play=1',headers={**self.headers,
                    'X-Alexa-Request-Type':kind,'X-Alexa-Intent-Name':name})
                self.assertEqual(result.status_code,200)
                self.assertNotIn('play',result.json)
                self.assertIsNotNone(server._peek_armed_play('echo'))
        finally:server._PLAYER_IDENTITY.reset(slot)

    def test_revoked_fresh_play_preflight_never_consumes_the_arm(self):
        server=self.server;slot=server._PLAYER_IDENTITY.set(server._player_identity_for_user_id(self.user['id']))
        try:
            server._arm_play('echo','AAAAAAAAAAA');self.access.revoke(self.user['id'])
            result=self.client.get('/api/alexa/identity/?include_play=1',headers={**self.headers,
                'X-Alexa-Request-Type':'IntentRequest','X-Alexa-Intent-Name':'PlaySongIntent'})
            self.assertEqual(result.status_code,403)
            self.assertIsNotNone(server._peek_armed_play('echo'))
        finally:server._PLAYER_IDENTITY.reset(slot)

    def launch(self,kind='play',age=0,processing=True):
        server=self.server;identity=server._player_identity_for_user_id(self.user['id'])
        token=server._PLAYER_IDENTITY.set(identity)
        try:
            server._now_playing.update(video_id='AAAAAAAAAAA',alexa_intent_at=time.time()-age-1,
                playback_processing=processing,queue=[dict(video_id='AAAAAAAAAAA',title='Target')],queue_index=0)
            server._playback_output.alexa('echo',wait=False)
            server._arm_play('echo','AAAAAAAAAAA',42000,kind=kind)
            server._ARMED_PLAYS['echo']['armed_at']-=age
            with mock.patch.object(server.Supporting,'get_stream',new=mock.AsyncMock(return_value={'audio_url':'https://music.example/audio'})):
                response=self.client.get('/api/alexa/identity/?include_control=1',headers={**self.headers,'X-Alexa-Request-Type':'LaunchRequest','X-MusicBox-Control-Launch':'1','X-MusicBox-Application-Id':'amzn1.ask.skill.11111111-1111-1111-1111-111111111111'})
            remaining=server._peek_armed_play('echo')
            return response,remaining
        finally:server._PLAYER_IDENTITY.reset(token)

    def test_launch_seek_consumes_once_and_bundles_position(self):
        response,remaining=self.launch()
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json['control']['offset_ms'],42000)
        self.assertIn('startup',response.json['control'])
        self.assertIsNone(remaining)

    def test_launch_resume_peeks_and_bundles_fresh_stream(self):
        response,remaining=self.launch(kind='resume')
        self.assertEqual(response.json['control']['offset_ms'],42000)
        self.assertIn('resume_stream',response.json['control'])
        self.assertIsNotNone(remaining)

    def test_launch_never_consumes_completed_or_old_or_queue_sync_intents(self):
        for options in ({'processing':False},{'age':31},{'kind':'queue_sync:old'}):
            response,remaining=self.launch(**options)
            self.assertNotIn('control',response.json)
            self.assertIsNotNone(remaining)

    def test_revoked_launch_cannot_consume_an_armed_selection(self):
        self.access.revoke(self.user['id'])
        response,remaining=self.launch()
        self.assertEqual(response.status_code,403)
        self.assertIsNotNone(remaining)
