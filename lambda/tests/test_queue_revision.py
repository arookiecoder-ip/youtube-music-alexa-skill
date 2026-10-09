"""Race and resume regressions against the real controller with SDK test doubles."""
import unittest
from unittest import mock
from test_resume_armed_play import player, _make_handler_input, _user_attr, _raw, _node, _armed, _played_stream


class QueueRevisionTests(unittest.TestCase):
    def handler(self):
        hi = _make_handler_input(_user_attr([_raw('A'), _raw('B'), _raw('C')], 0, [0, 1, 2],
            offset_ms=9000, stream_url='https://cached/A', stream_url_video_id='A'))
        info = player.Attributes.get_playback_info(hi)
        info['current_token'] = '0|A|attempt'
        hi.request_envelope.context.audio_player = _node(token=info['current_token'], player_activity='PLAYING')
        return hi, info

    def decision(self, hi, video='C'):
        info = player.Attributes.get_playback_info(hi)
        info['next_decision'] = dict(authoritative=True, permitted=True, decision_id='decision')
        return (player.player_models.Metadata(**_raw(video)) if video else None), None

    def test_same_song_resume_uses_latest_offset_and_cached_url(self):
        hi, info = self.handler()
        with mock.patch.object(player.Api, 'get_armed_play', return_value=_armed('A', offset_ms=45000)), mock.patch.object(player.Api, 'get_stream') as fetch:
            player.Controller.resume(hi, is_playback=True, guard_active=True)
        fetch.assert_not_called()
        self.assertEqual(_played_stream(hi).offset_in_milliseconds, 45000)
        self.assertEqual(_played_stream(hi).url, 'https://cached/A')

    def test_voice_resume_while_actually_playing_is_noop(self):
        hi, info = self.handler()
        with mock.patch.object(player.Api, 'get_armed_play', return_value=(None, None)):
            player.Controller.resume(hi, is_playback=True, guard_active=True)
        self.assertEqual(hi.response_builder.directives, [])

    def test_stale_nearly_finished_never_fetches(self):
        hi, info = self.handler()
        hi.request_envelope.request = _node(token='0|A|old')
        with mock.patch.object(player.Api, 'next_track') as fetch:
            self.assertFalse(player.Controller.enqueue_next_stream(hi))
        fetch.assert_not_called()

    def test_failed_lookup_does_not_enqueue_old_window(self):
        hi, info = self.handler()
        with mock.patch.object(player.Api, 'next_track', return_value=(None, Exception('offline'))):
            self.assertFalse(player.Controller.enqueue_next_stream(hi))
        self.assertEqual(hi.response_builder.directives, [])
        self.assertFalse(info['next_stream_enqueued'])

    def test_changed_decision_during_stream_lookup_is_rejected(self):
        hi, info = self.handler()
        with mock.patch.object(player.Api, 'next_track', side_effect=lambda *a: self.decision(hi)), mock.patch.object(player.Api, 'get_stream', return_value=(_node(audio_url='https://cached/C'), None)), mock.patch.object(player.Api, 'validate_next', return_value=False):
            self.assertFalse(player.Controller.enqueue_next_stream(hi))
        self.assertEqual(hi.response_builder.directives, [])

    def test_replace_pending_preserves_current_stream_token_and_offset(self):
        hi, info = self.handler()
        def stream(*args):
            info.update(stream_url='https://cached/C', stream_url_video_id='C')
            return _node(audio_url='https://cached/C'), None
        with mock.patch.object(player.Api, 'next_track', side_effect=lambda *a: self.decision(hi)), mock.patch.object(player.Api, 'get_stream', side_effect=stream), mock.patch.object(player.Api, 'validate_next', return_value=True):
            player.Controller.replace_pending(hi, '0|A|attempt')
        directive = hi.response_builder.directives[0]
        self.assertEqual(directive.play_behavior, 'REPLACE_ENQUEUED')
        self.assertFalse(hasattr(directive.audio_item.stream, 'expected_previous_token'))
        self.assertEqual(info['current_token'], '0|A|attempt')
        self.assertEqual(info['offset_in_ms'], 9000)
        self.assertEqual(info['stream_url'], 'https://cached/A')
        self.assertEqual(info['stream_url_video_id'], 'A')

    def test_replace_pending_after_device_advanced_is_noop(self):
        hi, info = self.handler()
        hi.request_envelope.context.audio_player.token = '1|B|new'
        with mock.patch.object(player.Api, 'next_track') as fetch:
            player.Controller.replace_pending(hi, '0|A|attempt')
        fetch.assert_not_called()

    def test_empty_queue_clears_buffer_only(self):
        hi, info = self.handler()
        with mock.patch.object(player.Api, 'next_track', side_effect=lambda *a: self.decision(hi, None)), mock.patch.object(player.Api, 'validate_next', return_value=True):
            player.Controller.replace_pending(hi, '0|A|attempt')
        self.assertEqual(hi.response_builder.directives[0].clear_behavior, 'CLEAR_ENQUEUED')
        self.assertEqual(info['current_token'], '0|A|attempt')

    def test_legacy_and_revision_tokens_parse(self):
        self.assertEqual(player._parse_token('A'), (None, 'A'))
        self.assertEqual(player._parse_token('4|A'), (4, 'A'))
        self.assertEqual(player._parse_token('4|A|revision'), (4, 'A'))

    def test_same_song_stale_stop_preserves_resume_position(self):
        hi, info = self.handler()
        hi.request_envelope.request = _node(token='0|A|old')
        self.assertTrue(player.Controller.record_stop(hi, 'A', 1000))
        self.assertEqual(info['offset_in_ms'], 9000)

    def test_consecutive_duplicate_song_keeps_current_slot(self):
        hi, info = self.handler()
        index = player.Controller._stage_next_track(hi, player.player_models.Metadata(**_raw('A')), 0)
        self.assertEqual(index, 1)
        self.assertEqual(info['index'], 0)
        self.assertEqual(player.Attributes.get_metadata_by_play_order(hi).video_id, 'A')
        self.assertEqual(player.Attributes.get_metadata_by_play_order(hi, index).video_id, 'A')
        self.assertNotEqual(info['play_order'][0], info['play_order'][1])

    def test_enqueue_preserves_current_cached_stream(self):
        hi, info = self.handler()
        def stream(*args):
            info.update(stream_url='https://cached/C', stream_url_video_id='C')
            return _node(audio_url='https://cached/C'), None
        with mock.patch.object(player.Api, 'next_track', side_effect=lambda *a: self.decision(hi)), mock.patch.object(player.Api, 'get_stream', side_effect=stream), mock.patch.object(player.Api, 'validate_next', return_value=True):
            self.assertTrue(player.Controller.enqueue_next_stream(hi))
        self.assertEqual(info['stream_url'], 'https://cached/A')
        self.assertEqual(info['stream_url_video_id'], 'A')
        self.assertEqual(hi.response_builder.directives[0].audio_item.stream.expected_previous_token, '0|A|attempt')

    def test_radio_tail_extension_revalidates_authoritative_next_once(self):
        hi, info = self.handler()
        calls = []
        def next_track(*args):
            calls.append(args)
            if len(calls) == 1:
                info['next_decision'] = dict(authoritative=True, permitted=True, allow_continuation=True)
                return None, None
            return self.decision(hi)
        with mock.patch.object(player.Api, 'next_track', side_effect=next_track), mock.patch.object(player.Controller, 'extend_radio_queue', return_value=True) as extend, mock.patch.object(player.Api, 'get_stream', return_value=(_node(audio_url='https://cached/C'),None)), mock.patch.object(player.Api, 'validate_next', return_value=True):
            self.assertTrue(player.Controller.enqueue_next_stream(hi))
        self.assertEqual(len(calls), 2)
        extend.assert_called_once()
        self.assertIn('|C|qdecision', hi.response_builder.directives[0].audio_item.stream.token)

    def test_empty_radio_continuation_is_bounded(self):
        hi, info = self.handler()
        def next_track(*args):
            info['next_decision'] = dict(authoritative=True, permitted=True, allow_continuation=True)
            return None, None
        with mock.patch.object(player.Api, 'next_track', side_effect=next_track), mock.patch.object(player.Controller, 'extend_radio_queue', return_value=False) as extend:
            self.assertFalse(player.Controller.enqueue_next_stream(hi))
        extend.assert_called_once()
        self.assertEqual(hi.response_builder.directives, [])
