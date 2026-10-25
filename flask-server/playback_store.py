"""Durable per-identity playback metadata; never restore an active ownership lease."""
import json
import sqlite3
import threading
import time
import uuid

from player_state import IDENTITY, OWNER


def stable_queue_entries(queue, previous=None):
    from collections import defaultdict, deque
    prior = defaultdict(deque)
    for item in previous or []:
        if item.get('entry_id'):
            prior[item.get('video_id')].append(item['entry_id'])
    seen = set()
    result = []
    for raw in queue or []:
        item = dict(raw)
        key = item.get('entry_id')
        if not isinstance(key, str) or not key or len(key) > 128 or key in seen:
            candidates = prior[item.get('video_id')]
            while candidates and candidates[0] in seen:
                candidates.popleft()
            key = candidates.popleft() if candidates else uuid.uuid4().hex
        item['entry_id'] = key
        seen.add(key)
        result.append(item)
    return result


class PlaybackStore:
    """One durable snapshot per playback identity.

    The table used to be a single row (`CHECK(id=1)`) holding the only queue that
    existed. Re-keying it by identity is what lets an account's queue survive a
    restart without ever being able to overwrite the owner's."""

    def __init__(self, path):
        self.path = path
        self.lock = threading.RLock()
        # identity -> (fingerprint, monotonic) of the last write, so a burst of
        # no-op saves coalesces per identity instead of across identities.
        self.saved = {}

    def _connection(self):
        conn = sqlite3.connect(self.path, timeout=10)
        conn.execute('CREATE TABLE IF NOT EXISTS playback_snapshot '
                     '(identity TEXT PRIMARY KEY, body TEXT NOT NULL)')
        self._rekey(conn)
        return conn

    def _rekey(self, conn):
        """Move a pre-multi-user single-row snapshot onto the owner identity."""
        columns = {row[1] for row in conn.execute('PRAGMA table_info(playback_snapshot)')}
        if not columns or 'identity' in columns:
            return
        conn.execute('ALTER TABLE playback_snapshot RENAME TO playback_snapshot_legacy')
        conn.execute('CREATE TABLE playback_snapshot (identity TEXT PRIMARY KEY, body TEXT NOT NULL)')
        conn.execute("INSERT INTO playback_snapshot SELECT ?, body FROM playback_snapshot_legacy",
                     (OWNER,))
        conn.execute('DROP TABLE playback_snapshot_legacy')

    def load(self, identity=OWNER):
        with self.lock, self._connection() as conn:
            row = conn.execute('SELECT body FROM playback_snapshot WHERE identity=?',
                               (identity,)).fetchone()
            if not row:
                return {}
            data = json.loads(row[0])
            data.update(playing=False, playback_processing=False, playback_confirmed=True, started_at=time.time())
            return data

    def save(self, identity, state, output, position):
        with self.lock:
            queue = stable_queue_entries(state.get('queue'))
            fingerprint = ([item['entry_id'] for item in queue], state.get('video_id'),
                           state.get('queue_index'), output.get('playback_output'), state.get('playing'))
            now = time.monotonic()
            previous = self.saved.get(identity)
            if previous and fingerprint == previous[0] and now - previous[1] < 5:
                return
            # Only durable metadata, never tokens, owners or lease deadlines.
            data = {key: state.get(key) for key in ('title', 'artist', 'artists', 'thumbnail',
                    'video_id', 'queue_index', 'duration_ms', 'playback_revision')}
            data.update(queue=queue, position_ms=max(0, int(position)),
                        preferred_output=output.get('playback_output'), output_serial=output.get('output_serial'))
            with self._connection() as conn:
                conn.execute('INSERT OR REPLACE INTO playback_snapshot VALUES (?, ?)',
                             (identity, json.dumps(data)))
            self.saved[identity] = (fingerprint, now)

class PlaybackPersistence:
    """A single coalescing writer keeps SQLite off request and ownership-heartbeat threads.

    Pending saves are kept per identity: one account's burst may only supersede
    its own queued snapshot, never evict another identity's."""
    def __init__(self, store, on_error=lambda: None):
        self.store = store
        self.on_error = on_error
        self.pending = {}
        self.guard = threading.Lock()
        self.wake = threading.Event()
        self.worker = threading.Thread(target=self._run, name='playback-persistence', daemon=True)
        self.worker.start()

    def submit(self, state, output, position, identity=None):
        # Caller holds its state lock; copy only persistent queue metadata.
        identity = identity or IDENTITY.get() or OWNER
        value = (identity, dict(state, queue=[dict(item) for item in state.get('queue') or []]),
                 dict(output), position)
        with self.guard:
            self.pending[identity] = value
        self.wake.set()

    def _run(self):
        while True:
            self.wake.wait()
            self.wake.clear()
            with self.guard:
                values, self.pending = list(self.pending.values()), {}
            for value in values:
                try: self.store.save(*value)
                except (sqlite3.Error, OSError, ValueError): self.on_error()
