import unittest
from unittest.mock import patch
from audio_credentials import AudioCredentials

class AudioCredentialsTests(unittest.TestCase):
    def setUp(self):
        self.credentials = AudioCredentials('test-secret')
    def test_token_scope_and_session_revocation(self):
        token = self.credentials.issue('session')
        self.assertTrue(self.credentials.verify(token, '/audio/', lambda sid: sid == 'session'))
        self.assertFalse(self.credentials.verify(token, '/api/app/queue/', lambda sid: True))
        self.assertFalse(self.credentials.verify(token, '/audio/', lambda sid: False))
        self.assertFalse(self.credentials.verify(token + 'tampered', '/audio/', lambda sid: True))
    def test_playback_and_download_expiry_are_bounded(self):
        with patch('time.time', return_value=100000):
            play = self.credentials.issue('session')
            download = self.credentials.issue('session', download=True)
        with patch('time.time', return_value=110000):
            self.assertFalse(self.credentials.verify(play, '/audio/', lambda sid: True))
            self.assertTrue(self.credentials.verify(download, '/audio/', lambda sid: True))
        with patch('time.time', return_value=200000):
            self.assertFalse(self.credentials.verify(download, '/audio/', lambda sid: True))
