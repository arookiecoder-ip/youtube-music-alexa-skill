"""Isolated durable skill attributes; no AWS resources or credentials required."""
import hashlib
import json
import os
import re
import sqlite3
from contextlib import contextmanager

from ask_sdk_core.attributes_manager import AbstractPersistenceAdapter
from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
from skill_security import persistence_key


class SQLiteAdapter(AbstractPersistenceAdapter):
    def __init__(self, path, account_id, skill_id):
        if not account_id or not skill_id:
            raise ValueError('account and skill required')
        self.path, self.account_id, self.skill_id = path, account_id, skill_id
        os.makedirs(os.path.dirname(os.path.abspath(path)), mode=0o700, exist_ok=True)
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        os.chmod(path, 0o600)
        with self.connect() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS state (
                    account TEXT, skill TEXT, id TEXT, value TEXT NOT NULL,
                    PRIMARY KEY(account, skill, id));
                CREATE TABLE IF NOT EXISTS metadata (
                    account TEXT, skill TEXT, name TEXT, value TEXT NOT NULL,
                    PRIMARY KEY(account, skill, name));
                CREATE TABLE IF NOT EXISTS requests (
                    account TEXT, skill TEXT, id TEXT, digest TEXT NOT NULL,
                    created REAL NOT NULL, PRIMARY KEY(account, skill, id));
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=2)
        db.execute('PRAGMA busy_timeout=2000')
        try:
            with db:
                yield db
        finally:
            db.close()

    def _key(self, envelope):
        app = envelope.context.system.application.application_id
        if app != self.skill_id:
            raise ValueError('application scope mismatch')
        return persistence_key(envelope)

    def ready(self):
        with self.connect() as db:
            return db.execute('SELECT 1 FROM metadata WHERE account=? AND skill=? AND name=?',
                              (self.account_id, self.skill_id, 'import_complete')).fetchone() is not None

    def get_attributes(self, request_envelope):
        key = self._key(request_envelope)
        if not self.ready():
            raise RuntimeError('skill state migration incomplete')
        with self.connect() as db:
            row = db.execute('SELECT value FROM state WHERE account=? AND skill=? AND id=?',
                             (self.account_id, self.skill_id, key)).fetchone()
        return TypeDeserializer().deserialize(json.loads(row[0])) if row else {}

    def save_attributes(self, request_envelope, attributes):
        key = self._key(request_envelope)
        value = json.dumps(TypeSerializer().serialize(attributes), separators=(',', ':'))
        if len(value.encode()) > 400 * 1024:
            raise ValueError('state too large')
        if not self.ready():
            raise RuntimeError('skill state migration incomplete')
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO state VALUES (?,?,?,?)',
                       (self.account_id, self.skill_id, key, value))

    def delete_attributes(self, request_envelope):
        key = self._key(request_envelope)
        with self.connect() as db:
            db.execute('DELETE FROM state WHERE account=? AND skill=? AND id=?',
                       (self.account_id, self.skill_id, key))

    def import_snapshot(self, items, transfer_id):
        """One atomic complete snapshot. Incomplete/invalid imports never activate."""
        if not isinstance(items, list) or len(items) > 10000:
            raise ValueError('snapshot size')
        rows, seen = [], set()
        for item in items:
            key = item['id']['S']
            value = item['attributes']
            if not isinstance(key, str) or not 1 <= len(key) <= 2048 or key in seen:
                raise ValueError('snapshot key')
            if set(value) != {'M'} or not isinstance(TypeDeserializer().deserialize(value), dict):
                raise ValueError('snapshot value')
            encoded = json.dumps(value, separators=(',', ':'), sort_keys=True)
            if len(encoded.encode()) > 400 * 1024:
                raise ValueError('snapshot row size')
            seen.add(key)
            rows.append((self.account_id, self.skill_id, key, encoded))
        digest = hashlib.sha256(json.dumps(sorted(rows), separators=(',', ':')).encode()).hexdigest()
        marker = json.dumps({'transfer_id': transfer_id, 'count': len(rows), 'digest': digest}, sort_keys=True)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT value FROM metadata WHERE account=? AND skill=? AND name=?',
                             (self.account_id, self.skill_id, 'import_complete')).fetchone()
            if old:
                if old[0] != marker:
                    raise ValueError('snapshot already imported')
                return digest
            db.executemany('INSERT INTO state VALUES (?,?,?,?)', rows)
            db.execute('INSERT INTO metadata VALUES (?,?,?,?)',
                       (self.account_id, self.skill_id, 'import_complete', marker))
        return digest

    def claim_request(self, request_id, digest, now):
        # Durable at-most-once dispatch. A crash can suppress one request; Alexa/app
        # control watchdogs recover it with a new request ID rather than replay audio.
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM requests WHERE created < ?', (now - 600,))
            row = db.execute('SELECT digest FROM requests WHERE account=? AND skill=? AND id=?',
                             (self.account_id, self.skill_id, request_id)).fetchone()
            if row:
                if row[0] != digest:
                    raise ValueError('request ID reused with different content')
                return False
            count = db.execute('SELECT count(*) FROM requests').fetchone()[0]
            if count >= 10000:
                raise RuntimeError('request ledger capacity')
            db.execute('INSERT INTO requests VALUES (?,?,?,?,?)',
                       (self.account_id, self.skill_id, request_id, digest, now))
            return True

    def export_snapshot(self):
        if not self.ready():
            raise RuntimeError('skill state migration incomplete')
        with self.connect() as db:
            rows = db.execute('SELECT id,value FROM state WHERE account=? AND skill=? ORDER BY id',
                              (self.account_id, self.skill_id)).fetchall()
        items = [{'id': {'S': key}, 'attributes': json.loads(value)} for key, value in rows]
        if len(json.dumps(items).encode()) > 12 * 1024 * 1024:
            raise ValueError('snapshot too large')
        return items

    def import_device_snapshot(self, items, transfer_id, device_key, expected_devices):
        """Hosted roles permit GetItem but not Scan. Activate only the full fleet."""
        if (not isinstance(device_key, str) or not re.fullmatch(r'echo-v2:[0-9a-f]{64}', device_key)
                or type(expected_devices) is not int or not 1 <= expected_devices <= 32
                or not isinstance(items, list) or len(items) > 1):
            raise ValueError('device snapshot scope')
        value = None
        if items:
            item = items[0]
            if item['id'] != {'S': device_key} or set(item['attributes']) != {'M'}:
                raise ValueError('device snapshot key')
            TypeDeserializer().deserialize(item['attributes'])
            value = json.dumps(item['attributes'], separators=(',', ':'), sort_keys=True)
            if len(value.encode()) > 400 * 1024:
                raise ValueError('device snapshot size')
        digest = hashlib.sha256((value or '').encode()).hexdigest()
        scope = (self.account_id, self.skill_id)
        entry = json.dumps({'transfer_id': transfer_id, 'digest': digest}, sort_keys=True)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            previous = db.execute('SELECT value FROM metadata WHERE account=? AND skill=? AND name=?',
                                  (*scope, 'device:' + device_key)).fetchone()
            complete = db.execute('SELECT value FROM metadata WHERE account=? AND skill=? AND name=?',
                                  (*scope, 'import_complete')).fetchone()
            if previous:
                if previous[0] != entry:
                    raise ValueError('conflicting device snapshot')
            else:
                if complete:
                    raise ValueError('migration already complete')
                if value is not None:
                    db.execute('INSERT INTO state VALUES (?,?,?,?)', (*scope, device_key, value))
                db.execute('INSERT INTO metadata VALUES (?,?,?,?)', (*scope, 'device:' + device_key, entry))
            entries = db.execute("SELECT value FROM metadata WHERE account=? AND skill=? AND name LIKE 'device:%'",
                                 scope).fetchall()
            if any(json.loads(row[0])['transfer_id'] != transfer_id for row in entries):
                raise ValueError('conflicting transfer')
            if len(entries) >= expected_devices and not complete:
                db.execute('INSERT INTO metadata VALUES (?,?,?,?)', (*scope, 'import_complete',
                           json.dumps({'transfer_id': transfer_id, 'devices': len(entries)})))
        return digest
