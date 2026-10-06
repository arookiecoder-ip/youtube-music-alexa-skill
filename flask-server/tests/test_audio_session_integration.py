"""Production middleware + token issuance + Range serving with a temporary session database."""
import os
import tempfile
import time
import unittest
from unittest.mock import patch
from test_audio_endpoint import server, VID

class ScopedAudioIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.audio = os.path.join(self.directory.name, VID + '.m4a')
        with open(self.audio, 'wb') as target:
            target.write(b'audio-data' * 100)
        for name, value in [('DB_FILE', os.path.join(self.directory.name, 'sessions.db')),
                            ('_db_initialized', False), ('_valid_sids', None)]:
            patcher = patch.object(server, name, value)
            patcher.start(); self.addCleanup(patcher.stop)
        server._ensure_db()
        with server.get_db() as conn:
            conn.execute('INSERT INTO web_sessions VALUES (?, ?)', ('owner-session', time.time()))
        self.owner = server.app.test_client()
        with self.owner.session_transaction() as session:
            session['remote_user'] = server.REMOTE_USER
            session['sid'] = 'owner-session'
        self.client = server.app.test_client()

    def test_logged_in_token_streams_ranges_but_cannot_mutate_queue_and_revokes(self):
        response = self.owner.post('/api/app/audio-token/', json={})
        self.assertEqual(response.status_code, 200)
        token = response.json['token']
        headers = {'X-MusicBox-Audio-Token': token, 'Range': 'bytes=0-8'}
        with patch.object(server.Supporting, 'cached_audio_path', return_value=self.audio), patch.object(server, '_is_dead_video', return_value=False):
            response = self.client.get('/audio/?video_id=' + VID, headers=headers)
            self.assertEqual(response.status_code, 206)
            self.assertEqual(response.data, b'audio-dat')
            response.close()
            self.assertEqual(self.client.post('/api/app/queue/', json={}, headers=headers).status_code, 401)
            server._session_close('owner-session')
            self.assertEqual(self.client.get('/audio/?video_id=' + VID, headers=headers).status_code, 401)

    def test_unchanged_poll_omits_queue_but_mutations_send_complete_metadata(self):
        state = dict(server._now_playing, queue=[{'video_id': VID, 'title': 'Song'}])
        headers = {'X-Api-Key': server.API_KEY}
        with patch.object(server, '_now_playing', state), patch.object(server, '_queue_seen_obj', None), patch.object(server, '_queue_version', 0):
            first = self.client.get('/alexa/now_playing/?serial=echo', headers=headers).json
            self.assertEqual(len(first['queue']), 1)
            slim = self.client.get('/alexa/now_playing/?serial=echo&queue_version=' + str(first['queue_version']), headers=headers).json
            self.assertNotIn('queue', slim)
            state['queue'] = [dict(state['queue'][0]), {'video_id': 'bbbbbbbbbbb'}]
            changed = self.client.get('/alexa/now_playing/?serial=echo&queue_version=' + str(first['queue_version']), headers=headers).json
            self.assertEqual(len(changed['queue']), 2)
            self.assertEqual(first['queue'][0]['entry_id'], changed['queue'][0]['entry_id'])
            self.assertGreater(changed['queue_version'], first['queue_version'])

    def test_machine_key_cannot_mint_owner_tokens(self):
        response = self.client.post('/api/app/audio-token/', json={}, headers={'X-Api-Key': server.API_KEY})
        self.assertEqual(response.status_code, 401)
        self.assertNotIn('token', response.json)
