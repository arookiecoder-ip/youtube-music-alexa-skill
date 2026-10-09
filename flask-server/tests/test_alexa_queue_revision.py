"""Protocol-2 decisions must be invalidated by queue and ownership changes."""
from unittest import mock
from test_next_track import NextTrackBase, _meta, server


class AlexaQueueRevisionTests(NextTrackBase):
    def setUp(self):
        super().setUp()
        self.output = dict(playback_output='alexa', output_token='owner1', output_epoch='epoch1', output_serial=self.SERIAL)
        self.patch = mock.patch.object(server._playback_output, 'snapshot', side_effect=lambda: dict(self.output))
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self._set_queue([_meta('A'), _meta('B'), _meta('C')])
        server._now_playing.update(alexa_token='0|A|attempt', alexa_intent_at=10,
            playing=True, playback_confirmed=True)

    def decision(self, **extra):
        return self.client.get('/next_track/', query_string=dict(after='A', token='0|A|attempt', protocol='2', key=server.API_KEY, **extra)).json

    def test_decision_valid_until_queue_changes(self):
        decision = self.decision()
        self.assertEqual(decision['track']['video_id'], 'B')
        self.assertTrue(self.decision(validate=decision['decision_id'])['valid'])
        self._set_queue([_meta('A'), _meta('C'), _meta('B')])
        self.assertFalse(self.decision(validate=decision['decision_id'])['valid'])
        self.assertEqual(self.decision()['track']['video_id'], 'C')

    def test_pause_intent_invalidates_inflight_decision(self):
        decision = self.decision()
        server._now_playing['alexa_intent_at'] = 11
        self.assertFalse(self.decision(validate=decision['decision_id'])['valid'])

    def test_device_switch_invalidates_inflight_decision(self):
        decision = self.decision()
        self.output['output_token'] = 'owner2'
        self.assertFalse(self.decision(validate=decision['decision_id'])['valid'])
        self.output['playback_output'] = 'phone'
        self.assertIsNone(self.decision()['track'])
        self.assertFalse(self.decision()['permitted'])

    def test_old_same_song_token_cannot_enqueue(self):
        server._now_playing['alexa_token'] = '0|A|new_attempt'
        self.assertFalse(self.decision()['permitted'])

    def test_legacy_endpoint_shape_preserved(self):
        response = self._get('A').json
        self.assertEqual(set(response), {'track'})
        self.assertEqual(response['track']['video_id'], 'B')

    def test_stale_stop_does_not_poison_event_watermark(self):
        server._now_playing['alexa_event_at'] = 10
        response = self.client.post('/alexa/state_event/', query_string={'key': server.API_KEY}, json={
            'event': 'stopped', 'token': '0|A|old', 'video_id': 'A',
            'event_timestamp': '2026-10-09T12:00:00.000Z'})
        self.assertEqual(response.json['ignored'], 'stale playback token')
        self.assertTrue(server._now_playing['playing'])
        self.assertEqual(server._now_playing['alexa_event_at'], 10)

    def timer(self):
        timer = mock.Mock()
        def make(delay, fire):
            timer.fire = fire
            return timer
        return timer, mock.patch.object(server.threading, 'Timer', side_effect=make)

    def test_queue_edit_replaces_buffer_without_playback_watchdog(self):
        self.decision()
        self._set_queue([_meta('A'), _meta('C'), _meta('B')])
        timer, patch = self.timer()
        with mock.patch.object(server, '_ARMED_PLAYS', {}), patch, mock.patch.object(server.alexa_remote.remote, 'play_video_id', create=True, return_value=None) as dispatch:
            server._schedule_alexa_queue_sync()
            timer.start.assert_called_once()
            timer.fire()
            dispatch.assert_called_once_with(self.SERIAL, 'A', 0)
            self.assertEqual(server._ARMED_PLAYS[self.SERIAL]['kind'], 'queue_sync:0|A|attempt')

    def test_queue_sync_never_steals_new_play_arm(self):
        self.decision()
        self._set_queue([_meta('A'), _meta('C')])
        timer, patch = self.timer()
        with mock.patch.object(server, '_ARMED_PLAYS', {}), patch, mock.patch.object(server.alexa_remote.remote, 'play_video_id', create=True) as dispatch:
            server._schedule_alexa_queue_sync()
            server._arm_play(self.SERIAL, 'C')
            timer.fire()
            dispatch.assert_not_called()
            self.assertEqual(server._ARMED_PLAYS[self.SERIAL]['kind'], 'play')

    def test_pause_after_scheduling_cancels_queue_sync(self):
        self.decision()
        self._set_queue([_meta('A'), _meta('C')])
        timer, patch = self.timer()
        with patch, mock.patch.object(server.alexa_remote.remote, 'play_video_id', create=True) as dispatch:
            server._schedule_alexa_queue_sync()
            server._now_playing['playing'] = False
            timer.fire()
            dispatch.assert_not_called()

    def test_stale_buffer_started_is_corrected_once(self):
        decision = self.decision()
        self._set_queue([_meta('A'), _meta('C'), _meta('B')])
        token = '1|B|q' + decision['decision_id']
        with mock.patch.object(server, '_schedule_play_dispatch') as dispatch, mock.patch.object(server, '_notify_sse'):
            response = server._reconcile_buffered_alexa_start(token)
            self.assertTrue(response['correcting'])
            self.assertTrue(response['stop'])
            self.assertEqual(server._now_playing['video_id'], 'C')
            self.assertEqual(dispatch.call_args.args, (self.SERIAL, 'C'))
            self.assertEqual(dispatch.call_args.kwargs['expected_output'], 'owner1')
            second = server._reconcile_buffered_alexa_start(token)
            self.assertFalse(second['stop'])
            dispatch.assert_called_once()

    def test_superseded_buffer_does_not_reclaim_phone(self):
        decision = self.decision()
        self.output['playback_output'] = 'phone'
        with mock.patch.object(server, '_schedule_play_dispatch') as dispatch:
            response = server._reconcile_buffered_alexa_start('1|B|q' + decision['decision_id'])
        self.assertTrue(response['stop'])
        self.assertEqual(server._now_playing['video_id'], 'A')
        dispatch.assert_not_called()

    def test_unchanged_buffer_is_accepted(self):
        decision = self.decision()
        self.assertTrue(server._reconcile_buffered_alexa_start('1|B|q' + decision['decision_id'])['accepted'])

    def test_deleted_successor_clears_playback_without_advancing(self):
        decision = self.decision()
        self._set_queue([_meta('A')])
        response = server._reconcile_buffered_alexa_start('1|B|q' + decision['decision_id'])
        self.assertTrue(response['stop'])
        self.assertEqual(response['ignored'], 'queue ended')
        self.assertFalse(server._now_playing['playing'])

    def test_authoritative_end_allows_radio_only_at_live_tail(self):
        self._set_queue([_meta('A')])
        decision = self.decision()
        self.assertTrue(decision['allow_continuation'])
        server._now_playing['playing'] = False
        self.assertFalse(self.decision()['permitted'])

    def test_radio_continuation_appends_to_live_tail(self):
        self._set_queue([_meta('A')])
        with mock.patch.object(server.Supporting, 'get_radio_queue', new=mock.AsyncMock(return_value=[_meta('A'),_meta('B'),_meta('C')])), mock.patch.object(server, '_notify_sse'):
            response = self.client.get('/get_radio/', query_string=dict(video_id='A', continuation='1',token='0|A|attempt',key=server.API_KEY))
        self.assertEqual(response.status_code, 200)
        self.assertEqual([item['video_id'] for item in response.json['playlist']], ['B','C'])
        self.assertEqual([item['video_id'] for item in server._now_playing['queue']], ['A','B','C'])

    def test_radio_result_cannot_overwrite_queue_edit_during_lookup(self):
        self._set_queue([_meta('A')])
        async def radio(seed):
            self._set_queue([_meta('A'), _meta('C')])
            return [_meta('A'),_meta('B')]
        with mock.patch.object(server.Supporting, 'get_radio_queue', side_effect=radio), mock.patch.object(server, '_notify_sse'):
            response = self.client.get('/get_radio/', query_string=dict(video_id='A',continuation='1',token='0|A|attempt',key=server.API_KEY))
        self.assertEqual(response.json['playlist'], [])
        self.assertEqual([item['video_id'] for item in server._now_playing['queue']], ['A','C'])

    def test_natural_finish_before_started_does_not_stop_successor(self):
        decision = self.decision()
        server._now_playing.update(playing=False, _alexa_finished_token='0|A|attempt')
        self.assertTrue(server._reconcile_buffered_alexa_start('1|B|q' + decision['decision_id'])['accepted'])

    def test_new_same_song_voice_attempt_rejects_old_buffer(self):
        decision = self.decision()
        server._now_playing['alexa_token'] = '0|A|new_voice_attempt'
        response = server._reconcile_buffered_alexa_start('1|B|q' + decision['decision_id'])
        self.assertTrue(response['stop'])
        self.assertNotIn('correcting', response)

    def test_correction_timer_cannot_override_later_pause(self):
        timer, patch = self.timer()
        with patch, mock.patch.object(server, '_arm_play') as arm:
            server._schedule_play_dispatch(self.SERIAL, 'A', expected_intent=10, expected_output='owner1')
            server._now_playing['playing'] = False
            timer.fire()
        arm.assert_not_called()

    def test_stream_yields_audio_before_producer_closes_pipe(self):
        import io
        import os
        from concurrent.futures import ThreadPoolExecutor
        reader_fd, writer_fd = os.pipe()
        reader = io.BufferedReader(os.fdopen(reader_fd, 'rb', buffering=0))
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(server._read_stream_chunk, reader)
            try:
                os.write(writer_fd, b'audio' * 200)
                self.assertEqual(future.result(timeout=2), b'audio' * 200)
            finally:
                os.close(writer_fd)
                reader.close()

    def test_stream_raw_reader_fallback_preserves_bytes(self):
        import io
        class Raw:
            def __init__(self):
                self.pipe = io.BytesIO(b'audio')
            def read(self, count):
                return self.pipe.read(count)
        self.assertEqual(server._read_stream_chunk(Raw()), b'audio')

    def test_metadata_refresh_does_not_invalidate_or_dispatch_queue_sync(self):
        decision = self.decision()
        queue = [dict(item, title='Updated title') for item in server._now_playing['queue']]
        self._set_queue(queue)
        self.assertTrue(self.decision(validate=decision['decision_id'])['valid'])
        with mock.patch.object(server.threading, 'Timer') as timer:
            server._schedule_alexa_queue_sync()
        timer.assert_not_called()

    def test_consecutive_duplicate_uses_authoritative_occurrence(self):
        self._set_queue([_meta('A'), _meta('A'), _meta('C')])
        decision = self.decision()
        result = server._reconcile_buffered_alexa_start('1|A|q' + decision['decision_id'])
        self.assertTrue(result['accepted'])
        self.assertEqual(result['queue_index'], 1)
        self.assertNotEqual(result['queue'][0]['entry_id'], result['queue'][1]['entry_id'])
