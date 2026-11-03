"""Real authentication middleware: role, tenant and revocation access matrix."""
import os
import sys
from pathlib import Path
from unittest import mock
sys.path[:0]=[str(Path(__file__).parent),str(Path(__file__).parents[1])]
from test_login_second_factor import SecondFactorDoorTestBase, PLAYER_PASSWORD


class RouteContractTests(SecondFactorDoorTestBase):
    def setUp(self):
        super().setUp();self.bind_player_door();self.server._ensure_db();self.user=self.player_user('contract-user')

    def login_player(self):
        response=self.client.post('/login/',json={'username':self.user['username'],'password':PLAYER_PASSWORD})
        self.assertEqual(response.status_code,200)

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
