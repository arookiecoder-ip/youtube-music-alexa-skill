"""Offline encrypted restore/key-rotation checks with synthetic secrets only."""
import importlib.util
from pathlib import Path
import tempfile
import sqlite3
import unittest
from cryptography.fernet import Fernet
from cryptography.exceptions import InvalidTag

spec=importlib.util.spec_from_file_location('security_storage',Path(__file__).resolve().parents[2]/'scripts/security_storage.py')
storage=importlib.util.module_from_spec(spec);spec.loader.exec_module(storage)


class EncryptedStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.roots={name:self.root/name for name in storage.ROOTS}
        for root in self.roots.values():root.mkdir()
        (self.roots['profiles']/'synthetic-cookie').write_text('private-synthetic-cookie')
        with sqlite3.connect(self.roots['data']/'data.db') as conn:
            conn.execute('CREATE TABLE user_sessions (token TEXT)');conn.execute("INSERT INTO user_sessions VALUES ('stale-session')")
            conn.execute('CREATE TABLE users (security_version INTEGER, totp_pending_enc TEXT, totp_pending_at REAL)');conn.execute("INSERT INTO users VALUES (1,'pending',1)")
        self.key=b'x'*32;self.archive=self.root/'backup.enc'

    def test_restore_authenticates_and_revokes_old_sessions(self):
        storage.backup(self.archive,self.key,self.roots)
        self.assertNotIn(b'private-synthetic-cookie',self.archive.read_bytes())
        dest=self.root/'restored';storage.restore(self.archive,self.key,dest)
        self.assertEqual((dest/'profiles/synthetic-cookie').read_text(),'private-synthetic-cookie')
        with sqlite3.connect(dest/'data/data.db') as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM user_sessions').fetchone()[0],0)
            self.assertGreater(conn.execute('SELECT security_version FROM users').fetchone()[0],1)

    def test_tampered_backup_extracts_nothing(self):
        storage.backup(self.archive,self.key,self.roots)
        data=bytearray(self.archive.read_bytes());data[-20]^=1;self.archive.write_bytes(data)
        destination=self.root/'rejected'
        with self.assertRaises(InvalidTag):storage.restore(self.archive,self.key,destination)
        self.assertFalse(destination.exists())

    def test_restore_refuses_nonempty_destination(self):
        storage.backup(self.archive,self.key,self.roots)
        dest=self.root/'occupied';dest.mkdir();(dest/'keep').write_text('safe')
        with self.assertRaises(ValueError):storage.restore(self.archive,self.key,dest)
        self.assertEqual((dest/'keep').read_text(),'safe')

    def test_rotation_is_atomic_and_invalidates_sessions(self):
        from cryptography.fernet import InvalidToken
        import base64
        db=self.root/'rotation.db'
        old=Fernet(base64.urlsafe_b64encode(self.key)); new_key=b'y'*32
        encrypted=old.encrypt(b'synthetic-provider-secret').decode()
        with sqlite3.connect(db) as conn:
            conn.execute('CREATE TABLE users (security_version INTEGER, totp_secret_enc TEXT, totp_pending_enc TEXT, totp_pending_at REAL)')
            conn.execute('INSERT INTO users VALUES (1, ?, ?, 1)',(encrypted,encrypted))
            conn.execute('CREATE TABLE user_provider_credentials (secret_enc TEXT)')
            conn.execute("INSERT INTO user_provider_credentials VALUES ('invalid-ciphertext')")
            conn.execute('CREATE TABLE user_email_tokens (code_enc TEXT)')
            conn.execute('CREATE TABLE user_sessions (token TEXT)')
            conn.execute("INSERT INTO user_sessions VALUES ('old-session')")
        with self.assertRaises(InvalidToken):storage.rotate(db,self.key,new_key)
        with sqlite3.connect(db) as conn:
            self.assertEqual(conn.execute('SELECT totp_secret_enc FROM users').fetchone()[0],encrypted)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM user_sessions').fetchone()[0],1)
            conn.execute('UPDATE user_provider_credentials SET secret_enc=?',(encrypted,))
        storage.rotate(db,self.key,new_key)
        new=Fernet(base64.urlsafe_b64encode(new_key))
        with sqlite3.connect(db) as conn:
            version,secret,pending=conn.execute('SELECT security_version,totp_secret_enc,totp_pending_enc FROM users').fetchone()
            self.assertEqual(new.decrypt(secret.encode()),b'synthetic-provider-secret')
            self.assertEqual((version,pending),(2,''))
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM user_sessions').fetchone()[0],0)
