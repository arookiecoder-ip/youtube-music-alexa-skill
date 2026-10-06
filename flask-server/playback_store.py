"""Durable single-user playback metadata; never restore an active ownership lease."""
import json
import sqlite3
import threading
import time
import uuid


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
    def __init__(self, path):
        self.path = path
        self.lock = threading.RLock()
        self.last_saved = 0
        self.fingerprint = None

    def _connection(self):
        conn = sqlite3.connect(self.path, timeout=10)
        conn.execute('CREATE TABLE IF NOT EXISTS playback_snapshot (id INTEGER PRIMARY KEY CHECK(id=1), body TEXT NOT NULL)')
        return conn

    def load(self):
        with self.lock, self._connection() as conn:
            row = conn.execute('SELECT body FROM playback_snapshot WHERE id=1').fetchone()
            if not row:
                return {}
            data = json.loads(row[0])
            data.update(playing=False, playback_processing=False, playback_confirmed=True, started_at=time.time())
            return data

    def save(self, state, output, position):
        with self.lock:
            queue = stable_queue_entries(state.get('queue'))
            fingerprint = ([item['entry_id'] for item in queue], state.get('video_id'),
                           state.get('queue_index'), output.get('playback_output'), state.get('playing'))
            now = time.monotonic()
            if fingerprint == self.fingerprint and now - self.last_saved < 5:
                return
            # Only durable metadata, never tokens, owners or lease deadlines.
            data = {key: state.get(key) for key in ('title', 'artist', 'artists', 'thumbnail',
                    'video_id', 'queue_index', 'duration_ms', 'playback_revision')}
            data.update(queue=queue, position_ms=max(0, int(position)),
                        preferred_output=output.get('playback_output'), output_serial=output.get('output_serial'))
            with self._connection() as conn:
                conn.execute('INSERT OR REPLACE INTO playback_snapshot VALUES (1, ?)', (json.dumps(data),))
            self.fingerprint, self.last_saved = fingerprint, now

class PlaybackPersistence:
    """A single coalescing writer keeps SQLite off request and ownership-heartbeat threads."""
    def __init__(self, store, on_error=lambda: None):
        import queue
        self.store = store
        self.on_error = on_error
        self.pending = queue.Queue(maxsize=1)
        self.worker = threading.Thread(target=self._run, name='playback-persistence', daemon=True)
        self.worker.start()

    def submit(self, state, output, position):
        import queue
        # Caller holds its state lock; copy only persistent queue metadata.
        value = (dict(state, queue=[dict(item) for item in state.get('queue') or []]), dict(output), position)
        try:
            self.pending.put_nowait(value)
        except queue.Full:
            try: self.pending.get_nowait()
            except queue.Empty: pass
            self.pending.put_nowait(value)

    def _run(self):
        while True:
            value = self.pending.get()
            try: self.store.save(*value)
            except (sqlite3.Error, OSError, ValueError): self.on_error()
