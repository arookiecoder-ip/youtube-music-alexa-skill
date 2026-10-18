import unittest
from playback_contract import restore_snapshot


class PlaybackContractTests(unittest.TestCase):
    def test_rollback_restores_content_with_new_revision(self):
        previous = dict(video_id='a', playing=True, playback_processing=False, playback_revision=4)
        staged = dict(video_id='b', playing=False, playback_processing=True, playback_revision=5)
        restore_snapshot(staged, previous)
        self.assertEqual(staged['video_id'], 'a')
        self.assertTrue(staged['playing'])
        self.assertFalse(staged['playback_processing'])
        self.assertEqual(staged['playback_revision'], 6)
        self.assertEqual(previous['playback_revision'], 4)

    def test_missing_legacy_revisions_and_repeated_rollbacks_advance(self):
        state = {'video_id': 'a'}
        restore_snapshot(state, {'video_id': 'b'})
        self.assertEqual(state['playback_revision'], 1)
        restore_snapshot(state, {'video_id': 'a'})
        self.assertEqual(state['playback_revision'], 2)
