"""Verified skill-subject pairing and narrowly scoped Echo stream capabilities.

A binding is keyed by the Alexa skill that proved it as well as the Alexa
subject and device.  Two custom skills installed on the same Echo therefore get
two independent bindings: pairing the legacy skill never authorizes the
multi-user skill (or the other way around).  The empty namespace is the legacy
skill, which does not send an ``X-Alexa-Skill-Id`` header, so existing bindings
keep working unchanged.
"""
import hashlib
import hmac
import re
import secrets
import time
from itsdangerous import URLSafeTimedSerializer, BadData

# The only shape the backend accepts as a namespace.  Rejecting everything else
# keeps malformed client input out of binding keys without granting anything:
# the namespace is a partition, not an authorization proof.
_SKILL_ID = re.compile(
    r'^amzn1\.ask\.skill\.[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-'
    r'[0-9a-f]{4}-[0-9a-f]{12}$', re.IGNORECASE)


class AlexaIdentity:
    def __init__(self, accounts, secret):
        self.accounts, self.db = accounts, accounts.db
        self.secret = str(secret).encode()
        self.signer = URLSafeTimedSerializer(secret, salt='musicbox-echo-stream-v1')

    @staticmethod
    def skill_namespace(value):
        """Normalize a skill-id header.

        ``None`` means the header was present but malformed and the request must
        be rejected.  ``''`` is the legacy namespace, used when the header is
        absent.
        """
        if value is None or value == '':
            return ''
        value = str(value).strip()
        return value.lower() if _SKILL_ID.match(value) else None

    def digest(self, value, skill=''):
        # The legacy namespace must keep hashing exactly as it did before skill
        # scoping existed, otherwise every binding (and live pairing code)
        # created by the first skill stops resolving after the upgrade.  Only a
        # real skill id adds the partitioning prefix.
        payload = value if not skill else f'{skill}\0{value}'
        return hmac.new(self.secret, payload.encode(),
                        hashlib.sha256).hexdigest()

    def issue(self, user):
        code = str(secrets.randbelow(10**8)).zfill(8)
        now = time.time()
        with self.db.connect() as conn:
            conn.execute('DELETE FROM alexa_pair_codes WHERE user_id=? OR expires_at<?', (user['id'], now))
            conn.execute('INSERT INTO alexa_pair_codes VALUES (?,?,?,?)',
                (self.digest(code), user['id'], now+300, self.accounts.security_version(user['id'])))
        return code

    def pair(self, code, subject, device, skill=''):
        if not subject or not device or skill is None or not isinstance(code, str) or len(code) != 8 or not code.isascii() or not code.isdigit():
            return False
        with self.db.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute("SELECT c.*,u.security_version AS live_version FROM alexa_pair_codes c JOIN users u ON u.id=c.user_id WHERE c.code_hash=? AND u.status='active' AND u.must_change_password=0", (self.digest(code),)).fetchone()
            if not row or row['expires_at'] <= time.time() or row['security_version'] != row['live_version']:
                return False
            conn.execute('DELETE FROM alexa_pair_codes WHERE code_hash=?', (row['code_hash'],))
            conn.execute('INSERT OR REPLACE INTO alexa_bindings (skill_id, subject_hash, device_hash, user_id) VALUES (?,?,?,?)',
                         (skill, self.digest(subject, skill), self.digest(device, skill), row['user_id']))
        return True

    def resolve(self, subject, device, skill=''):
        if not subject or not device or skill is None:
            return None
        row = self.db.fetchone(
            'SELECT user_id FROM alexa_bindings WHERE skill_id=? AND subject_hash=? AND device_hash=?',
            (skill, self.digest(subject, skill), self.digest(device, skill)))
        user = self.accounts.get_user(row['user_id']) if row else None
        return user if user and user['status'] == 'active' and not user['must_change_password'] else None

    def stream_token(self, user_id, video):
        return self.signer.dumps({'user_id': user_id, 'video': video, 'version': self.accounts.security_version(user_id)})

    def stream_user(self, token, video):
        try:
            data = self.signer.loads(token, max_age=7200)
        except BadData:
            return None
        if data.get('video') != video or not data.get('version') or data['version'] != self.accounts.security_version(data.get('user_id')):
            return None
        user = self.accounts.get_user(data['user_id'])
        return user if user and user['status'] == 'active' and not user['must_change_password'] else None
