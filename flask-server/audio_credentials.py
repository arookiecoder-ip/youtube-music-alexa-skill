"""Scoped, expiring audio credentials; cannot authorize queue or account mutations."""
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

AUDIO_PATHS = {'/audio', '/get_radio', '/queue_tracks', '/next_track'}

class AudioCredentials:
    def __init__(self, secret):
        self.signer = URLSafeTimedSerializer(secret, salt='music-box-audio-v2')

    def issue(self, session_id, download=False, user_id=None, security_version=None):
        return self.signer.dumps({'sid': session_id, 'scope': 'audio', 'user_id': user_id, 'security_version': security_version, 'ttl': 86400 if download else 7200})

    def verify(self, token, path, session_valid, subject_valid=None):
        if path.rstrip('/') not in AUDIO_PATHS:
            return False
        try:
            data = self.signer.loads(token, max_age=86400)
            self.signer.loads(token, max_age=int(data.get('ttl', 0)))
            return (data.get('scope') == 'audio' and session_valid(data.get('sid'))
                    and (subject_valid is None or subject_valid(data)))
        except (BadSignature, SignatureExpired, ValueError, TypeError):
            return False
