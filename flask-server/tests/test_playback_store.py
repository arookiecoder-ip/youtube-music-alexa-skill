import tempfile
import unittest
from playback_store import PlaybackStore, stable_queue_entries

class PlaybackStoreTests(unittest.TestCase):
    def test_duplicate_occurrences_have_stable_distinct_ids(self):
        rows = stable_queue_entries([{'video_id': 'a'}, {'video_id': 'a'}])
        self.assertNotEqual(rows[0]['entry_id'], rows[1]['entry_id'])
        self.assertEqual(stable_queue_entries(list(reversed(rows)))[0]['entry_id'], rows[1]['entry_id'])
        self.assertEqual(len({r['entry_id'] for r in stable_queue_entries([rows[0], rows[0]])}), 2)

    def test_restart_preserves_cursor_metadata_without_active_lease(self):
        with tempfile.TemporaryDirectory() as directory:
            path = directory + '/state.db'
            store = PlaybackStore(path)
            store.save({'queue': [{'video_id': 'a'}], 'video_id': 'a', 'queue_index': 0,
                        'playing': True}, {'playback_output': 'phone', 'output_token': 'secret',
                                         'output_serial': 'echo'}, 12345)
            restored = PlaybackStore(path).load()
            self.assertEqual(restored['position_ms'], 12345)
            self.assertEqual(restored['queue_index'], 0)
            self.assertFalse(restored['playing'])
            self.assertFalse(restored['playback_processing'])
            self.assertNotIn('output_token', restored)
            self.assertEqual(restored['preferred_output'], 'phone')
