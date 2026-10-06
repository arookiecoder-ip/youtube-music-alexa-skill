"""Scoped, expiring audio credentials; cannot authorize queue or account mutations."""
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

AUDIO_PATHS = {'/audio', '/get_radio', '/queue_tracks', '/next_track'}

class AudioCredentials:
    def __init__(self, secret):
        self.signer = URLSafeTimedSerializer(secret, salt='music-box-audio-v1')

    def issue(self, session_id, download=False):
        return self.signer.dumps({'sid': session_id, 'scope': 'audio', 'ttl': 86400 if download else 7200})

    def verify(self, token, path, session_valid):
        if path.rstrip('/') not in AUDIO_PATHS:
            return False
        try:
            data = self.signer.loads(token, max_age=86400)
            self.signer.loads(token, max_age=int(data.get('ttl', 0)))
            return data.get('scope') == 'audio' and session_valid(data.get('sid'))
        except (BadSignature, SignatureExpired, ValueError, TypeError):
            return False
