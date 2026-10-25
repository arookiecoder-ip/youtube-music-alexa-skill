import contextlib
import json
import sqlite3
import tempfile
import threading
import unittest

from player_state import IDENTITY
from playback_store import PlaybackPersistence, PlaybackStore, stable_queue_entries

class PlaybackStoreTests(unittest.TestCase):
    def test_duplicate_occurrences_have_stable_distinct_ids(self):
        rows = stable_queue_entries([{'video_id': 'a'}, {'video_id': 'a'}])
        self.assertNotEqual(rows[0]['entry_id'], rows[1]['entry_id'])
        self.assertEqual(stable_queue_entries(list(reversed(rows)))[0]['entry_id'], rows[1]['entry_id'])
        self.assertEqual(len({r['entry_id'] for r in stable_queue_entries([rows[0], rows[0]])}), 2)

    def test_legacy_skill_metadata_refresh_keeps_existing_occurrence_ids(self):
        before = stable_queue_entries([{'video_id': 'a'}, {'video_id': 'b'}, {'video_id': 'a'}])
        after = stable_queue_entries([{'video_id': 'b'}, {'video_id': 'a'}, {'video_id': 'a'}], before)
        self.assertEqual([row['entry_id'] for row in after], [before[1]['entry_id'], before[0]['entry_id'], before[2]['entry_id']])

    def test_restart_preserves_cursor_metadata_without_active_lease(self):
        with tempfile.TemporaryDirectory() as directory:
            path = directory + '/state.db'
            store = PlaybackStore(path)
            store.save('owner', {'queue': [{'video_id': 'a'}], 'video_id': 'a', 'queue_index': 0,
                                 'playing': True}, {'playback_output': 'phone', 'output_token': 'secret',
                                                    'output_serial': 'echo'}, 12345)
            restored = PlaybackStore(path).load('owner')
            self.assertEqual(restored['position_ms'], 12345)
            self.assertEqual(restored['queue_index'], 0)
            self.assertFalse(restored['playing'])
            self.assertFalse(restored['playback_processing'])
            self.assertNotIn('output_token', restored)
            self.assertEqual(restored['preferred_output'], 'phone')

    def test_one_identity_can_never_overwrite_another_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            path = directory + '/state.db'
            store = PlaybackStore(path)
            store.save('owner', {'queue': [{'video_id': 'mine'}], 'video_id': 'mine'},
                       {'playback_output': 'alexa', 'output_serial': 'echo'}, 10)
            store.save('user:a', {'queue': [{'video_id': 'theirs'}], 'video_id': 'theirs'},
                       {'playback_output': 'alexa', 'output_serial': 'other'}, 20)
            self.assertEqual(store.load('owner')['video_id'], 'mine')
            self.assertEqual(store.load('user:a')['video_id'], 'theirs')
            self.assertEqual(store.load('user:missing'), {})

    def test_coalescing_is_per_identity(self):
        # An unchanged snapshot inside the five-second window is a no-op for its
        # own identity, but must not silence a different identity's first write.
        with tempfile.TemporaryDirectory() as directory:
            store = PlaybackStore(directory + '/state.db')
            state = {'queue': [{'video_id': 'a'}], 'video_id': 'a'}
            output = {'playback_output': 'alexa', 'output_serial': 'echo'}
            store.save('owner', state, output, 10)
            store.save('owner', state, output, 10)
            store.save('user:a', state, output, 10)
            with contextlib.closing(sqlite3.connect(directory + '/state.db')) as conn:
                self.assertEqual([row[0] for row in conn.execute(
                    'SELECT identity FROM playback_snapshot ORDER BY identity')], ['owner', 'user:a'])

    def test_legacy_single_row_snapshot_becomes_the_owner(self):
        with tempfile.TemporaryDirectory() as directory:
            path = directory + '/state.db'
            body = json.dumps({'video_id': 'legacy',
                               'queue': [{'video_id': 'legacy', 'entry_id': 'x' * 32}],
                               'queue_index': 0, 'position_ms': 900, 'output_serial': 'echo'})
            with contextlib.closing(sqlite3.connect(path)) as conn:
                conn.execute('CREATE TABLE playback_snapshot '
                             '(id INTEGER PRIMARY KEY CHECK(id=1), body TEXT NOT NULL)')
                conn.execute('INSERT INTO playback_snapshot VALUES (1, ?)', (body,))
                conn.commit()
            restored = PlaybackStore(path).load('owner')
            self.assertEqual(restored['video_id'], 'legacy')
            self.assertEqual(restored['position_ms'], 900)
            self.assertEqual(PlaybackStore(path).load('user:a'), {})


class RecordingStore:
    def __init__(self, expected):
        self.saved = []
        self.done = threading.Event()
        self.expected = expected

    def save(self, identity, state, output, position):
        self.saved.append((identity, state['video_id']))
        if len(self.saved) >= self.expected:
            self.done.set()


class PlaybackPersistenceTests(unittest.TestCase):
    def setUp(self):
        IDENTITY.set('owner')
        self.addCleanup(IDENTITY.set, 'owner')

    def test_a_burst_from_one_identity_does_not_drop_another_pending_save(self):
        # The old writer held one slot and overwrote it when full, which was fine
        # for a single player and silently loses a queue once several exist.
        store = RecordingStore(3)
        writer = PlaybackPersistence(store)
        for identity in ('owner', 'user:a', 'user:b'):
            writer.submit({'queue': [], 'video_id': identity}, {'output_serial': ''}, 0,
                          identity=identity)
        self.assertTrue(store.done.wait(3))
        self.assertEqual(sorted(item[0] for item in store.saved), ['owner', 'user:a', 'user:b'])

    def test_the_queued_identity_is_the_caller_not_the_worker_thread(self):
        # The persistence worker is a plain thread, so it never inherits the
        # request's identity; submit() has to capture it at call time.
        store = RecordingStore(1)
        writer = PlaybackPersistence(store)
        IDENTITY.set('user:queued')
        writer.submit({'queue': [], 'video_id': 'x'}, {'output_serial': ''}, 0)
        IDENTITY.set('owner')
        self.assertTrue(store.done.wait(3))
        self.assertEqual(store.saved, [('user:queued', 'x')])
