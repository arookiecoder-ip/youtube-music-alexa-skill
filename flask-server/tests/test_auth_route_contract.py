"""Real authentication middleware: role, tenant and revocation access matrix."""
import json
import os
import sys
from pathlib import Path
from unittest import mock
sys.path[:0]=[str(Path(__file__).parent),str(Path(__file__).parents[1])]
from test_login_second_factor import SecondFactorDoorTestBase, PLAYER_PASSWORD


class RouteContractTests(SecondFactorDoorTestBase):
    def setUp(self):
        super().setUp();self.bind_player_door();self.server._ensure_db();self.user=self.player_user('contract-user');self.layer.alexa_access.set_enabled(self.user['id'], True)

    def login_player(self):
        response=self.client.post('/login/',json={'username':self.user['username'],'password':PLAYER_PASSWORD})
        self.assertEqual(response.status_code,200)

    def test_history_missing_tracking_remains_pending_without_server_error(self):
        self.login_player()
        provider = mock.Mock(auth_type='BROWSER')
        with mock.patch.object(self.server, '_get_ytmusic_home', return_value=provider):
            for song in ({'videoDetails': {'videoId': 'AAAAAAAAAAA'}},
                         {'playbackTracking': None},
                         {'playbackTracking': {'videostatsPlaybackUrl': {}}}):
                provider.get_song.return_value = song
                response = self.client.post('/api/track/AAAAAAAAAAA/listen', json={})
                self.assertEqual(response.status_code, 409)
                self.assertEqual(response.json['error']['code'], 'history_sync_pending')
            provider.add_history_item.assert_not_called()
            song = {'playbackTracking': {'videostatsPlaybackUrl': {'baseUrl': 'https://music.youtube.com/tracking'}}}
            provider.get_song.return_value = song
            provider.add_history_item.return_value.status_code = 204
            response = self.client.post('/api/track/AAAAAAAAAAA/listen', json={})
            self.assertEqual(response.status_code, 200)
            provider.add_history_item.assert_called_once_with(song)
            provider.add_history_item.return_value.status_code = 500
            self.assertEqual(self.client.post('/api/track/AAAAAAAAAAA/listen', json={}).status_code, 502)

    def test_public_information_routes_render_without_login(self):
        for path, heading in [('/privacy_policy/', 'Privacy'), ('/terms_of_use/', 'Terms of use')]:
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertIn(heading.encode(), response.data)
            self.assertIn(b'width=device-width', response.data)

    def test_sleep_timer_accepts_session_for_alexa_and_requires_authentication(self):
        path = '/api/app/sleep-timer/'
        timers = mock.Mock()
        timers.get.return_value = None
        target = dict(playback_output='alexa', output_serial='test-echo',
                      output_owner='', handoff_pending=False)
        with mock.patch.object(self.server, '_playback_timers', timers), \
                mock.patch.object(self.server, '_playback_output', mock.Mock(snapshot=mock.Mock(return_value=target))):
            for method in ('get', 'post', 'delete'):
                response = getattr(self.client, method)(path, json={'minutes': 5})
                self.assertEqual(response.status_code, 401)
            timers.set.assert_not_called()
            timers.cancel.assert_not_called()
            self.login_player()
            self.assertEqual(self.client.get(path).status_code, 200)
            identity = 'user:' + self.user['id']
            timers.get.assert_called_with(identity)
            timers.set.return_value = {'deadline': 2000000000, 'target': json.dumps(target)}
            response = self.client.post(path, json={'minutes': 5})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json['playback_output'], 'alexa')
            timers.set.assert_called_once_with(identity, 5, target)
            self.assertEqual(self.client.delete(path, json={}).status_code, 200)
            timers.cancel.assert_called_once_with(identity)
            # The cookie alone must not permit cross-site form submissions.
            self.assertEqual(self.client.post(path, data={'minutes': 5}).status_code, 401)

    def test_cookie_authenticated_get_cannot_change_liked_songs(self):
        self.login_player()
        with mock.patch.object(self.server, '_get_ytmusic_home') as provider:
            response = self.client.get('/alexa/like/?video_id=AAAAAAAAAAA')
        self.assertEqual(response.status_code, 405)
        provider.assert_not_called()

    def test_paired_skill_get_and_browser_json_post_can_like(self):
        from types import SimpleNamespace
        provider = SimpleNamespace(auth_type='BROWSER', rate_song=mock.Mock())
        self.login_player()
        with mock.patch.object(self.server, '_get_ytmusic_home', return_value=provider):
            response = self.client.post('/alexa/like/', json={'video_id': 'AAAAAAAAAAA'})
        self.assertEqual(response.status_code, 200)
        provider.rate_song.assert_called_once_with('AAAAAAAAAAA', 'LIKE')
        identity = self.layer.alexa_identity
        self.assertTrue(identity.pair(identity.issue(self.user), 'subject', 'device'))
        provider.rate_song.reset_mock()
        headers = {'X-Api-Key': self.server.API_KEY, 'X-Alexa-User-Id': 'subject', 'X-Alexa-Device-Id': 'device'}
        with mock.patch.object(self.server, '_get_ytmusic_home', return_value=provider):
            response = self.client.get('/alexa/like/?video_id=AAAAAAAAAAA', headers=headers)
        self.assertEqual(response.status_code, 200)
        provider.rate_song.assert_called_once_with('AAAAAAAAAAA', 'LIKE')

    def test_account_login_clears_legacy_owner_identity(self):
        with self.client.session_transaction() as state:
            state['remote_user']=self.server.REMOTE_USER;state['sid']=self.server._session_open()
        self.login_player()
        with self.client.session_transaction() as state:self.assertNotIn('remote_user',state)
        self.accounts.revoke_all_sessions(self.user,self.user['id'])
        self.assertEqual(self.client.post('/api/app/audio-token/',json={}).status_code,401)

    def test_audio_capability_establishes_account_identity_and_revokes(self):
        self.login_player();token=self.client.post('/api/app/audio-token/',json={}).json['token']
        isolated=self.server.app.test_client()
        headers={'X-MusicBox-Audio-Token':token}
        endpoint='next_track'
        with mock.patch.dict(self.server.app.view_functions,{endpoint:lambda:{'identity':self.server._current_identity()}}):
            self.assertEqual(isolated.get('/next_track/',headers=headers).json['identity'],'user:'+self.user['id'])
            self.assertEqual(isolated.post('/api/app/queue/',headers=headers,json={}).status_code,401)
            self.accounts.revoke_all_sessions(self.user,self.user['id'])
            self.assertEqual(isolated.get('/next_track/',headers=headers).status_code,401)

    def test_temporary_password_cannot_authorize_normal_playback(self):
        self.accounts.db.execute('UPDATE users SET must_change_password=1 WHERE id=?',(self.user['id'],))
        self.login_player()
        response=self.client.post('/api/app/output/',json={})
        self.assertEqual(response.status_code,403)
        self.assertEqual(response.json['error']['code'],'password_change_required')
        response=self.client.get('/home')
        self.assertEqual(response.status_code,302)
        self.assertIn('/account',response.headers['Location'])

    def test_unmapped_skill_subject_never_defaults_to_owner(self):
        headers={'X-Api-Key':self.server.API_KEY,'X-Alexa-User-Id':'unknown-user','X-Alexa-Device-Id':'unknown-device'}
        response=self.client.get('/armed_play/',headers=headers)
        self.assertEqual(response.status_code,403)
        self.assertEqual(response.json['error']['code'],'link_account_required')

    def test_paired_subject_is_bound_to_the_linked_account(self):
        code=self.layer.alexa_identity.issue(self.user)
        self.assertTrue(self.layer.alexa_identity.pair(code,'subject','device'))
        headers={'X-Api-Key':self.server.API_KEY,'X-Alexa-User-Id':'subject','X-Alexa-Device-Id':'device'}
        with mock.patch.dict(self.server.app.view_functions,{'armed_play':lambda:{'identity':self.server._current_identity()}}):
            response=self.client.get('/armed_play/',headers=headers)
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.json['identity'],'user:'+self.user['id'])
