#!/usr/bin/env python3
"""Offline authenticated backup/restore and provider-key rotation.

Stop the application and browser before using this tool. Paths and keys are
explicit; this tool never discovers credentials or writes to a live deployment.
"""
import argparse
import base64
import contextlib
import io
import os
from pathlib import Path
import secrets
import sqlite3
import tarfile
import tempfile

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.fernet import Fernet

MAGIC = b'MUSICBOX-BACKUP-1\n'
ROOTS = {'data', 'alexa', 'profiles'}


def read_key(path):
    key = base64.urlsafe_b64decode(Path(path).read_bytes().strip())
    if len(key) != 32:
        raise ValueError('Expected a base64-encoded 32-byte key')
    return key


class EncryptWriter:
    def __init__(self, output, key):
        self.output = output
        nonce = secrets.token_bytes(12)
        output.write(MAGIC + nonce)
        self.encryptor = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
        self.encryptor.authenticate_additional_data(MAGIC)
    def write(self, data):
        self.output.write(self.encryptor.update(data))
        return len(data)
    def finish(self):
        self.output.write(self.encryptor.finalize())
        self.output.write(self.encryptor.tag)


def backup(output, key, roots):
    with tempfile.TemporaryDirectory(prefix='musicbox-backup-') as tmp:
        snapshot = Path(tmp) / 'data.db'
        db = roots.get('data', Path('/nonexistent')) / 'data.db'
        if db.is_file():
            with contextlib.closing(sqlite3.connect(db)) as source, contextlib.closing(sqlite3.connect(snapshot)) as target:
                source.backup(target)
        descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, 'wb') as target:
                writer = EncryptWriter(target, key)
                with tarfile.open(mode='w|', fileobj=writer) as archive:
                    for name, root in roots.items():
                        if name not in ROOTS or not root.is_dir():
                            raise ValueError('Expected an existing data/alexa/profiles directory')
                        for path in sorted(root.rglob('*')):
                            if path.is_symlink() or not (path.is_file() or path.is_dir()):
                                continue
                            relative = path.relative_to(root)
                            # SQLite snapshots replace the main database; runtime journals
                            # and Chromium singleton sockets are never portable state.
                            if name == 'data' and (path.name.startswith('data.db-') or path.name == 'data.db'):
                                continue
                            if path.name.startswith('Singleton'):
                                continue
                            archive.add(path, arcname=f'{name}/{relative.as_posix()}', recursive=False)
                        if name == 'data' and snapshot.exists():
                            archive.add(snapshot, arcname='data/data.db')
                writer.finish()
        except BaseException:
            Path(output).unlink(missing_ok=True)
            raise


def invalidate_restored_sessions(db):
    with contextlib.closing(sqlite3.connect(db)) as conn, conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'users' in tables:
            columns = {row[1] for row in conn.execute('PRAGMA table_info(users)')}
            if 'security_version' in columns:
                conn.execute("UPDATE users SET security_version=security_version+?, totp_pending_enc='', totp_pending_at=0", (secrets.randbelow(2**31)+1,))
        for table in ('user_sessions', 'web_sessions', 'jam_tokens', 'user_email_tokens', 'alexa_pair_codes', 'alexa_bindings'):
            if table in tables:
                conn.execute(f'DELETE FROM {table}')
        if 'kv' in tables:
            conn.execute("INSERT OR REPLACE INTO kv(k,v) VALUES('sessions_revoked_all','1')")


def restore(source, key, destination):
    destination = Path(destination)
    if destination.exists() and any(destination.iterdir()):
        raise ValueError('Restore destination must be empty')
    # Authenticate the entire archive before extraction; never stream unauthenticated
    # GCM plaintext into application volumes.
    with tempfile.TemporaryDirectory(prefix='musicbox-restore-') as tmp:
        plain = Path(tmp) / 'archive.tar'
        with open(source, 'rb') as encrypted, open(plain, 'wb') as target:
            if encrypted.read(len(MAGIC)) != MAGIC:
                raise ValueError('Unknown backup format')
            nonce = encrypted.read(12)
            encrypted.seek(-16, os.SEEK_END); tag = encrypted.read(16)
            remaining = encrypted.tell() - 16 - len(MAGIC) - 12
            encrypted.seek(len(MAGIC)+12)
            decryptor = Cipher(algorithms.AES(key), modes.GCM(nonce, tag)).decryptor()
            decryptor.authenticate_additional_data(MAGIC)
            while remaining:
                block = encrypted.read(min(65536, remaining))
                if not block:
                    raise ValueError('Truncated backup')
                target.write(decryptor.update(block)); remaining -= len(block)
            target.write(decryptor.finalize())
        stage = Path(tmp) / 'stage'; stage.mkdir(mode=0o700)
        with tarfile.open(plain) as archive:
            members = archive.getmembers()
            for member in members:
                name = Path(member.name)
                if name.is_absolute() or '..' in name.parts or not name.parts or name.parts[0] not in ROOTS or not (member.isfile() or member.isdir()):
                    raise ValueError('Unsafe backup entry')
            archive.extractall(stage, members=members, filter='data')
        db = stage / 'data/data.db'
        if db.exists():
            invalidate_restored_sessions(db)
        destination.mkdir(mode=0o700, parents=True, exist_ok=True)
        for path in stage.iterdir():
            import shutil
            shutil.move(str(path), destination / path.name)


def rotate(db, old_key, new_key):
    old, new = Fernet(base64.urlsafe_b64encode(old_key)), Fernet(base64.urlsafe_b64encode(new_key))
    with contextlib.closing(sqlite3.connect(db)) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        for table, columns in [('users', ('totp_secret_enc','totp_pending_enc')), ('user_provider_credentials',('secret_enc',)), ('user_email_tokens',('code_enc',))]:
            for column in columns:
                for rowid, value in conn.execute(f'SELECT rowid, {column} FROM {table} WHERE {column}<>\'\'').fetchall():
                    conn.execute(f'UPDATE {table} SET {column}=? WHERE rowid=?', (new.encrypt(old.decrypt(value.encode())).decode(),rowid))
        invalidate = 'UPDATE users SET security_version=security_version+1, totp_pending_enc=\'\', totp_pending_at=0'
        conn.execute(invalidate)
        conn.execute('DELETE FROM user_sessions')
        conn.execute('DELETE FROM user_email_tokens')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    create=sub.add_parser('backup'); create.add_argument('--output',type=Path,required=True);create.add_argument('--key-file',required=True)
    for name in ROOTS:create.add_argument('--'+name,type=Path,required=True)
    recover=sub.add_parser('restore');recover.add_argument('--source',type=Path,required=True);recover.add_argument('--destination',type=Path,required=True);recover.add_argument('--key-file',required=True)
    change=sub.add_parser('rotate');change.add_argument('--database',type=Path,required=True);change.add_argument('--old-key-file',required=True);change.add_argument('--new-key-file',required=True)
    args=parser.parse_args()
    if args.command=='backup':backup(args.output,read_key(args.key_file),{name:getattr(args,name) for name in ROOTS})
    elif args.command=='restore':restore(args.source,read_key(args.key_file),args.destination)
    else:rotate(args.database,read_key(args.old_key_file),read_key(args.new_key_file))


if __name__=='__main__':main()
