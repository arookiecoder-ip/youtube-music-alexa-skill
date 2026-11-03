"""Verified skill-subject pairing and narrowly scoped Echo stream capabilities."""
import hashlib
import hmac
import secrets
import time
from itsdangerous import URLSafeTimedSerializer, BadData


class AlexaIdentity:
    def __init__(self, accounts, secret):
        self.accounts, self.db = accounts, accounts.db
        self.secret = str(secret).encode()
        self.signer = URLSafeTimedSerializer(secret, salt='musicbox-echo-stream-v1')

    def digest(self, value):
        return hmac.new(self.secret, str(value).encode(), hashlib.sha256).hexdigest()

    def issue(self, user):
        code = str(secrets.randbelow(10**8)).zfill(8)
        now=time.time()
        with self.db.connect() as conn:
            conn.execute('DELETE FROM alexa_pair_codes WHERE user_id=? OR expires_at<?', (user['id'],now))
            conn.execute('INSERT INTO alexa_pair_codes VALUES (?,?,?,?)',
                (self.digest(code),user['id'],now+300,self.accounts.security_version(user['id'])))
        return code

    def pair(self, code, subject, device):
        if not subject or not device or not isinstance(code,str) or len(code)!=8 or not code.isascii() or not code.isdigit():
            return False
        with self.db.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row=conn.execute("SELECT c.*,u.security_version AS live_version FROM alexa_pair_codes c JOIN users u ON u.id=c.user_id WHERE c.code_hash=? AND u.status='active' AND u.must_change_password=0", (self.digest(code),)).fetchone()
            if not row or row['expires_at']<=time.time() or row['security_version']!=row['live_version']:
                return False
            conn.execute('DELETE FROM alexa_pair_codes WHERE code_hash=?',(row['code_hash'],))
            conn.execute('INSERT OR REPLACE INTO alexa_bindings VALUES (?,?,?)',(self.digest(subject),self.digest(device),row['user_id']))
        return True

    def resolve(self, subject, device):
        row=self.db.fetchone('SELECT user_id FROM alexa_bindings WHERE subject_hash=? AND device_hash=?',(self.digest(subject),self.digest(device)))
        user=self.accounts.get_user(row['user_id']) if row else None
        return user if user and user['status']=='active' and not user['must_change_password'] else None

    def stream_token(self,user_id,video):
        return self.signer.dumps({'user_id':user_id,'video':video,'version':self.accounts.security_version(user_id)})

    def stream_user(self,token,video):
        try:
            data=self.signer.loads(token,max_age=7200)
        except BadData:
            return None
        if data.get('video')!=video or not data.get('version') or data['version']!=self.accounts.security_version(data.get('user_id')):
            return None
        user=self.accounts.get_user(data['user_id'])
        return user if user and user['status']=='active' and not user['must_change_password'] else None
