"""Revocable per-account Alexa credentials; never store a usable token."""
import hashlib
import hmac
import secrets
import time
import requests

AMAZON_ENDPOINTS = frozenset(('https://api.amazonalexa.com',
    'https://api.eu.amazonalexa.com', 'https://api.fe.amazonalexa.com',
    'https://api.fe.amazon.com'))
# Tokens cannot access account management, provider cookies, admin or arbitrary URLs.
SKILL_PATHS = frozenset(('/api/alexa/identity', '/armed_play', '/next_track',
    '/alexa/state_event', '/find_stream_list', '/stream_playlist', '/stream_video',
    '/get_stream', '/get_playlist_info', '/get_radio', '/queue_tracks',
    '/alexa/like', '/play_genre'))

class AccessDenied(Exception):
    def __init__(self, code='alexa_access_denied'):
        self.code = code
        super().__init__('Alexa access denied. Check account access, email and token.')

class AlexaAccess:
    def __init__(self, accounts):
        self.accounts, self.db = accounts, accounts.db

    def enabled(self, user_id):
        user = self.accounts.get_user(user_id)
        return bool(user and user.get('alexa_enabled') and user['status'] == 'active'
                    and not user['must_change_password'])

    @staticmethod
    def eligible(user):
        return bool(user and user.get('alexa_enabled') and user['status'] == 'active'
            and not user['must_change_password'] and user.get('email_verified') and user.get('email'))

    def set_enabled(self, user_id, enabled):
        with self.db.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            conn.execute('UPDATE users SET alexa_enabled=?, updated_at=? WHERE id=?',
                         (int(enabled), time.time(), user_id))
            if not enabled:
                conn.execute('UPDATE alexa_access_tokens SET revoked_at=? WHERE user_id=? AND revoked_at=0', (time.time(), user_id))
                conn.execute('DELETE FROM alexa_bindings WHERE user_id=?', (user_id,))

    def revoke(self, user_id):
        self.db.execute('UPDATE alexa_access_tokens SET revoked_at=? WHERE user_id=? AND revoked_at=0', (time.time(), user_id))

    def rotate(self, user_id):
        raw = 'mbx_' + secrets.token_hex(12) + '.' + secrets.token_urlsafe(32)
        with self.db.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute('SELECT * FROM users WHERE id=?', (user_id,)).fetchone()
            user = dict(row) if row else None
            if not self.eligible(user):
                raise ValueError('Enable Alexa access and use an active account with a verified email and a permanent password first.')
            now = time.time()
            conn.execute('UPDATE alexa_access_tokens SET revoked_at=? WHERE user_id=? AND revoked_at=0', (now, user_id))
            conn.execute('INSERT INTO alexa_access_tokens (id,user_id,token_hash,email,security_version,created_at,expires_at) VALUES (?,?,?,?,?,?,?)',
                (raw.split('.')[0], user_id, hashlib.sha256(raw.encode()).hexdigest(), user['email'].strip().casefold(), user['security_version'], now, now + 365*86400))
        return raw

    def metadata(self, user_id):
        return [dict(r) for r in self.db.fetchall('SELECT id,created_at,expires_at,revoked_at FROM alexa_access_tokens WHERE user_id=? ORDER BY created_at DESC LIMIT 5', (user_id,))]

    def current(self, token_id):
        row = self.db.fetchone('SELECT * FROM alexa_access_tokens WHERE id=?', (token_id,))
        user = self.accounts.get_user(row['user_id']) if row else None
        if not row or row['revoked_at'] or row['expires_at'] <= time.time() or not self.eligible(user):
            raise AccessDenied()
        if row['security_version'] != user['security_version'] or row['email'] != user['email'].strip().casefold():
            raise AccessDenied()
        return user, dict(row)

    def authenticate(self, headers):
        raw = headers.get('Authorization', '').removeprefix('Bearer ')
        if len(raw) > 256 or not raw.startswith('mbx_'):
            raise AccessDenied()
        user, token = self.current(raw.split('.')[0])
        if not hmac.compare_digest(token['token_hash'], hashlib.sha256(raw.encode()).hexdigest()):
            raise AccessDenied()
        if headers.get('X-MusicBox-Email', '').strip().casefold() != token['email']:
            raise AccessDenied('amazon_email_mismatch')
        endpoint = headers.get('X-Alexa-Api-Endpoint', '').rstrip('/')
        proof = headers.get('X-Alexa-Profile-Token', '')
        if endpoint not in AMAZON_ENDPOINTS or not proof or len(proof) > 16384:
            raise AccessDenied('amazon_permission_required')
        try:
            response = requests.get(endpoint + '/v2/accounts/~current/settings/Profile.email',
                headers={'Authorization': 'Bearer ' + proof}, timeout=(1, 1.5), allow_redirects=False)
            if response.status_code != 200:
                raise AccessDenied('amazon_permission_required')
            email = response.json()
        except (requests.RequestException, ValueError):
            raise AccessDenied('amazon_profile_unavailable') from None
        if not isinstance(email, str) or email.strip().casefold() != token['email']:
            raise AccessDenied('amazon_email_mismatch')
        # Recheck after the network call: an admin may have revoked it in flight.
        user, token = self.current(token['id'])
        token['version'] = hashlib.sha256((token['id'] + ':' + str(token['security_version'])).encode()).hexdigest()
        return user, token
