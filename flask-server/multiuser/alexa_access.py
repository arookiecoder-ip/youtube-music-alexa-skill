"""Revocable per-account Alexa credentials; never store a usable token."""
from collections import OrderedDict
import hashlib
import re
import threading
import hmac
import json
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
        self._proofs, self._proof_lock = OrderedDict(), threading.Lock()
        self._profile_clients = threading.local()
        self._profile_transport = requests.adapters.HTTPAdapter(pool_connections=4, pool_maxsize=16)

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

    def linked_amazon(self, user_id):
        account = self.accounts.get_provider_secret(user_id, 'amazon')
        if (account.get('verified_by') != 'amazon_authenticated_session'
                or not account.get('email') or not account.get('customer_id')
                or not account.get('generation')):
            raise AccessDenied('amazon_link_required')
        return account

    def link_amazon(self, user_id, email, customer_id):
        """Called only with identity returned by the authenticated Amazon API."""
        if not isinstance(email, str) or '@' not in email or not isinstance(customer_id, str) or not customer_id:
            raise AccessDenied('amazon_link_required')
        email = email.strip().casefold()
        with self.db.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            user = conn.execute('SELECT * FROM users WHERE id=?', (user_id,)).fetchone()
            if not self.eligible(dict(user) if user else None):
                raise AccessDenied()
            old = conn.execute("SELECT secret_enc,status FROM user_provider_credentials WHERE user_id=? AND provider='amazon'", (user_id,)).fetchone()
            previous = {}
            if old and old['status'] == 'connected':
                try:
                    previous = self.accounts.secret_box.decrypt_json(old['secret_enc'])
                except Exception:
                    previous = {}
            generation = (previous.get('generation') if isinstance(previous, dict)
                and previous.get('email') == email and previous.get('customer_id') == customer_id else None)
            payload = {'verified_by':'amazon_authenticated_session','email':email,
                       'customer_id':customer_id,'generation':generation or secrets.token_hex(16)}
            now = time.time()
            conn.execute("INSERT INTO user_provider_credentials (user_id,provider,secret_enc,metadata_json,status,updated_at,last_validated_at) VALUES (?,'amazon',?,?,'connected',?,?) ON CONFLICT(user_id,provider) DO UPDATE SET secret_enc=excluded.secret_enc,metadata_json=excluded.metadata_json,status='connected',last_error='',updated_at=excluded.updated_at,last_validated_at=excluded.last_validated_at",
                         (user_id,self.accounts.secret_box.encrypt_json(payload),json.dumps({'account_email':email}),now,now))

    def unlink_amazon(self, user_id):
        self.db.execute("DELETE FROM user_provider_credentials WHERE user_id=? AND provider='amazon'", (user_id,))

    def _profile_get(self, url, **kwargs):
        # Pool TLS connections without sharing mutable Session state between
        # workers. Each bearer is request-local and cookies never carry over.
        client = getattr(self._profile_clients, 'client', None)
        if client is None:
            client = requests.Session()
            client.mount('https://', self._profile_transport)
            self._profile_clients.client = client
        client.cookies.clear()
        try:
            return client.get(url, **kwargs)
        finally:
            client.cookies.clear()

    def _proof_key(self, headers, user, token, linked, endpoint, proof):
        request_id = headers.get('X-Alexa-Request-Id', '')
        subject, device = headers.get('X-Alexa-User-Id', ''), headers.get('X-Alexa-Device-Id', '')
        if (not subject or not device or len(subject) > 512 or len(device) > 512
                or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,256}', request_id)):
            return None
        return (user['id'], token['id'], linked['generation'], endpoint, subject,
                device, request_id, hashlib.sha256(proof.encode()).digest())

    def _cached_proof(self, key):
        if key is None:
            return None
        with self._proof_lock:
            entry = self._proofs.get(key)
            if entry and entry[0] > time.monotonic():
                return entry[1]
            self._proofs.pop(key, None)
        return None

    def _remember_proof(self, key, email):
        if key is None:
            return
        with self._proof_lock:
            now = time.monotonic()
            for stale, entry in list(self._proofs.items()):
                if entry[0] <= now:
                    del self._proofs[stale]
            self._proofs[key] = (now + 10, email)
            while len(self._proofs) > 512:
                self._proofs.popitem(last=False)

    def authenticate(self, headers):
        raw = headers.get('Authorization', '').removeprefix('Bearer ')
        if len(raw) > 256 or not raw.startswith('mbx_'):
            raise AccessDenied()
        user, token = self.current(raw.split('.')[0])
        if not hmac.compare_digest(token['token_hash'], hashlib.sha256(raw.encode()).hexdigest()):
            raise AccessDenied()
        if headers.get('X-MusicBox-Email', '').strip().casefold() != token['email']:
            raise AccessDenied('musicbox_email_mismatch')
        linked = self.linked_amazon(user['id'])
        endpoint = headers.get('X-Alexa-Api-Endpoint', '').rstrip('/')
        proof = headers.get('X-Alexa-Profile-Token', '')
        if endpoint not in AMAZON_ENDPOINTS or not proof or len(proof) > 16384:
            raise AccessDenied('amazon_permission_required')
        # Share only successful Amazon proof within one Alexa envelope. Local
        # authorization is never cached: current() and the linked identity are
        # checked before and after every call, including a profile cache hit.
        proof_key = self._proof_key(headers, user, token, linked, endpoint, proof)
        email = self._cached_proof(proof_key)
        proof_was_cached = email is not None
        if email is None:
            try:
                response = self._profile_get(endpoint + '/v2/accounts/~current/settings/Profile.email',
                    headers={'Authorization': 'Bearer ' + proof}, timeout=(1, 1.5), allow_redirects=False)
                try:
                    if response.status_code != 200:
                        raise AccessDenied('amazon_permission_required')
                    email = response.json()
                finally:
                    response.close()
            except (requests.RequestException, ValueError):
                raise AccessDenied('amazon_profile_unavailable') from None
        if not isinstance(email, str) or email.strip().casefold() != linked['email']:
            raise AccessDenied('amazon_email_mismatch')
        # Recheck after the network call: an admin may have revoked it in flight.
        user, token = self.current(token['id'])
        current_link = self.linked_amazon(user['id'])
        if current_link['generation'] != linked['generation']:
            raise AccessDenied('amazon_link_changed')
        if not proof_was_cached:
            self._remember_proof(proof_key, email)
        token['amazon_generation'] = current_link['generation']
        token['version'] = hashlib.sha256((token['id'] + ':' + str(token['security_version']) + ':' + current_link['generation']).encode()).hexdigest()
        return user, token
