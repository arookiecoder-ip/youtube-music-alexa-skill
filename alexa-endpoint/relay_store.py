"""Request-local skill attributes plus a durable, account-scoped replay ledger."""
from contextlib import contextmanager
import base64
import json
import os
import sqlite3
import threading

from ask_sdk_core.attributes_manager import AbstractPersistenceAdapter
from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
from skill_security import persistence_key


def _attribute(value, *, encode):
    """Bridge AWS JSON base64 binary values and boto3's in-memory byte values."""
    if not isinstance(value, dict) or len(value) != 1:
        raise ValueError('invalid DynamoDB attribute')
    kind, data = next(iter(value.items()))
    if kind == 'M':
        data = {key: _attribute(item, encode=encode) for key, item in data.items()}
    elif kind == 'L':
        data = [_attribute(item, encode=encode) for item in data]
    elif kind in ('B', 'BS'):
        def convert(item):
            return (base64.b64encode(item).decode('ascii') if encode
                    else base64.b64decode(item, validate=True))
        data = convert(data) if kind == 'B' else [convert(item) for item in data]
    return {kind: data}


def deserialize_attributes(value):
    return TypeDeserializer().deserialize(_attribute(value, encode=False))


class RelayAdapter(AbstractPersistenceAdapter):
    def __init__(self):
        self.local = threading.local()

    @contextmanager
    def request(self, key, attributes):
        if hasattr(self.local, 'state'):
            raise RuntimeError('nested relay request')
        state = {'key': key, 'attributes': attributes, 'dirty': False}
        self.local.state = state
        try:
            yield state
        finally:
            del self.local.state

    def _state(self, envelope):
        state = getattr(self.local, 'state', None)
        if state is None or persistence_key(envelope) != state['key']:
            raise ValueError('relay persistence scope mismatch')
        return state

    def get_attributes(self, request_envelope):
        return deserialize_attributes(self._state(request_envelope)['attributes'])

    def save_attributes(self, request_envelope, attributes):
        state = self._state(request_envelope)
        value = _attribute(TypeSerializer().serialize(attributes), encode=True)
        if len(json.dumps(value).encode()) > 400 * 1024:
            raise ValueError('skill attributes exceed bounds')
        state.update(attributes=value, dirty=True)

    def delete_attributes(self, request_envelope):
        self.save_attributes(request_envelope, {})


class ReplayLedger:
    def __init__(self, path, account, skill):
        if not account or not skill:
            raise ValueError('account and skill required')
        self.path, self.scope = path, (account, skill)
        os.makedirs(os.path.dirname(os.path.abspath(path)), mode=0o700, exist_ok=True)
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        os.chmod(path, 0o600)
        with self.connect() as db:
            db.executescript('''PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS requests (
                    account TEXT, skill TEXT, id TEXT, digest TEXT NOT NULL,
                    created REAL NOT NULL, PRIMARY KEY(account, skill, id));''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=1)
        try:
            with db:
                yield db
        finally:
            db.close()

    def claim(self, request_id, digest, now):
        # At-most-once dispatch across restarts. A crash can suppress one
        # request; the app watchdog/new user command uses a fresh Alexa ID.
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM requests WHERE created < ?', (now - 600,))
            row = db.execute('SELECT digest FROM requests WHERE account=? AND skill=? AND id=?',
                             (*self.scope, request_id)).fetchone()
            if row:
                if row[0] != digest:
                    raise ValueError('conflicting replay')
                return False
            if db.execute('SELECT count(*) FROM requests').fetchone()[0] >= 10000:
                raise RuntimeError('replay ledger capacity')
            db.execute('INSERT INTO requests VALUES (?,?,?,?,?)', (*self.scope, request_id, digest, now))
            return True
