"""Offline release contract, run inside the exact image before production replacement."""
import hashlib
import sqlite3
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from multiuser.alexa_access import AlexaAccess, AccessDenied
from multiuser.db import Database


def validate():
    raw = 'mbx_contract.synthetic-fixture'
    user = dict(id='contract-user', status='active', alexa_enabled=True, must_change_password=False,
                email_verified=True, email='changed@example.test', security_version=1)
    token = dict(id='mbx_contract', user_id=user['id'], email='original@example.test',
                 token_hash=hashlib.sha256(raw.encode()).hexdigest(), security_version=1,
                 revoked_at=0, expires_at=time.time()+3600)
    accounts = Mock()
    accounts.get_user.return_value=user
    accounts.db.fetchone.return_value=token
    accounts.get_provider_secret.return_value=dict(verified_by='amazon_authenticated_session',
        email='amazon@example.test', customer_id='customer', generation='generation')
    access=AlexaAccess(accounts)
    access._profile_get=Mock(return_value=SimpleNamespace(status_code=200,
        json=lambda:'amazon@example.test', close=lambda:None))
    headers={'Authorization':'Bearer '+raw, 'X-MusicBox-Account-Id':user['id'],
             'X-Alexa-Profile-Token':'synthetic-proof', 'X-Alexa-Api-Endpoint':'https://api.amazonalexa.com',
             'X-Alexa-User-Id':'subject', 'X-Alexa-Device-Id':'device'}
    if access.authenticate(headers)[0]['id'] != user['id']:
        raise RuntimeError('Account-ID authentication contract failed')
    try:
        access.authenticate({**headers,'X-MusicBox-Account-Id':'different-account'})
    except AccessDenied:
        pass
    else:
        raise RuntimeError('Account-ID ownership validation failed')
    with tempfile.TemporaryDirectory(prefix='musicbox-release-contract-') as directory:
        db=Database(str(Path(directory)/'test.db'));db.initialize()
        with db.connect() as conn:
            conn.execute("INSERT INTO users (id,username,password_hash,created_at,updated_at) VALUES ('user','fixture','old',1,1)")
            conn.execute("DROP TRIGGER users_security_version")
            conn.execute("CREATE TRIGGER users_security_version AFTER UPDATE OF password_hash ON users BEGIN UPDATE users SET security_version=security_version+1 WHERE id=NEW.id; END")
            conn.execute('DELETE FROM multiuser_schema_migrations WHERE version=14')
        db._initialized=False;db.initialize()
        before=db.scalar("SELECT security_version FROM users WHERE id='user'")
        db.execute("UPDATE users SET password_hash='replacement' WHERE id='user'")
        if before != db.scalar("SELECT security_version FROM users WHERE id='user'"):
            raise RuntimeError('Deployed credential migration contract failed')
        with db.connect() as conn:
            conn.execute("INSERT INTO alexa_access_tokens (id,user_id,token_hash,email,security_version,created_at,expires_at) VALUES ('one','user','fixture','fixture@example.test',1,1,9999999999)")
            try:
                conn.execute("INSERT INTO alexa_access_tokens (id,user_id,token_hash,email,security_version,created_at,expires_at) VALUES ('two','user','fixture','fixture@example.test',1,2,9999999999)")
            except sqlite3.IntegrityError:
                pass
            else:
                raise RuntimeError('Single active Alexa token contract failed')
    print('PASS image contract: account ID, changed email, account ownership, upgrade and single active token.')


if __name__=='__main__':
    validate()
