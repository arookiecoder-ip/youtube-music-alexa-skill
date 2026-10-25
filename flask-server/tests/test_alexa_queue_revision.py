"""Protocol-2 decisions must be invalidated by queue and ownership changes."""
from unittest import mock
import threading
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

    def test_delete_at_boundary_old_start_then_manual_selection_dispatches_new_song(self):
        a,b,c,d = ('A'*11,'B'*11,'C'*11,'D'*11)
        self._set_queue([_meta(a),_meta(b),_meta(c),_meta(d)])
        server._now_playing.update(alexa_token='0|'+a+'|attempt',alexa_intent_at=10,
            playing=True,playback_confirmed=True,position_ms=179990)
        decision=self.client.get('/next_track/',query_string=dict(after=a,token='0|'+a+'|attempt',protocol='2',key=server.API_KEY)).json
        old_queue=list(server._now_playing['queue'])
        timers=[]
        def timer_factory(delay,fire):
            timer=mock.Mock();timer.fire=fire;timers.append(timer);return timer
        with mock.patch.object(server,'_PENDING_DISPATCH',{}), \
                mock.patch.object(server.threading,'Timer',side_effect=timer_factory), \
                mock.patch.object(server,'_notify_sse'),mock.patch.object(server,'_record_listen'), \
                mock.patch.object(server,'_ensure_audio_ready_for_play'),mock.patch.object(server,'_arm_play'), \
                mock.patch.object(server,'_note_trigger_sent'),mock.patch.object(server,'_trigger_in_flight',return_value=False), \
                mock.patch.object(server,'_dispatch_play_with_retry',return_value=None) as dispatch:
            removed=self.client.post('/alexa/queue_remove/',query_string={'key':server.API_KEY},json={'index':1,'video_id':b})
            self.assertEqual(removed.status_code,200)
            started=self.client.post('/alexa/state_event/',query_string={'key':server.API_KEY},json={
                'event':'started','video_id':b,'token':'1|'+b+'|q'+decision['decision_id'],
                'serial':self.SERIAL,'queue':old_queue,'queue_index':1})
            self.assertTrue(started.json['correcting'])
            correction=timers[-1]
            self.assertEqual(server._now_playing['video_id'],c)
            clicked=self.client.post('/alexa/play_queue/',query_string={'key':server.API_KEY},json={
                'serial':self.SERIAL,'video_id':d,'queue_index':2,'suppress_radio':True})
            self.assertEqual(clicked.status_code,200,clicked.json)
            newest=timers[-1]
            correction.fire()  # A cancelled timer can already be queued on another thread.
            newest.fire()
            dispatch.assert_called_once_with(self.SERIAL,d,0)
            self.assertEqual(server._get_now_playing()['video_id'],d)
            self.assertEqual([x['video_id'] for x in server._now_playing['queue']],[a,c,d])

    def test_buffered_acceptance_commits_before_delete_and_duplicate_cannot_restore_queue(self):
        a,b,c,d = ('A'*11,'B'*11,'C'*11,'D'*11)
        self._set_queue([_meta(a),_meta(b),_meta(c),_meta(d)])
        server._now_playing.update(alexa_token='0|'+a+'|attempt',alexa_intent_at=10,playing=True,playback_confirmed=True)
        decision=self.client.get('/next_track/',query_string=dict(after=a,token='0|'+a+'|attempt',protocol='2',key=server.API_KEY)).json
        token='1|'+b+'|q'+decision['decision_id'];old_queue=list(server._now_playing['queue'])
        with mock.patch.object(server,'_notify_sse'),mock.patch.object(server,'_record_listen'):
            first=self.client.post('/alexa/state_event/',query_string={'key':server.API_KEY},json={
                'event':'started','video_id':b,'token':token,'serial':self.SERIAL,'queue':old_queue,'queue_index':1})
            self.assertEqual(first.status_code,200)
            self.assertEqual(server._now_playing['queue_index'],1)
            active=self.client.post('/alexa/queue_remove/',query_string={'key':server.API_KEY},json={'index':1,'video_id':b})
            self.assertEqual(active.status_code,400)
            self.assertEqual(self.client.post('/alexa/queue_remove/',query_string={'key':server.API_KEY},json={'index':2,'video_id':c}).status_code,200)
            server._now_playing['position_ms']=12000
            duplicate=self.client.post('/alexa/state_event/',query_string={'key':server.API_KEY},json={
                'event':'started','video_id':b,'token':token,'serial':self.SERIAL,'queue':old_queue,'queue_index':1})
            self.assertEqual(duplicate.json['ignored'],'duplicate queued start')
            self.assertEqual(server._now_playing['position_ms'],12000)
            self.assertEqual([x['video_id'] for x in server._now_playing['queue']],[a,b,d])

    def test_finish_before_deleted_buffer_start_uses_live_successor(self):
        a,b,c = ('A'*11,'B'*11,'C'*11)
        self._set_queue([_meta(a),_meta(b),_meta(c)])
        server._now_playing.update(alexa_token='0|'+a+'|attempt',alexa_intent_at=10,playing=True,playback_confirmed=True)
        decision=self.client.get('/next_track/',query_string=dict(after=a,token='0|'+a+'|attempt',protocol='2',key=server.API_KEY)).json
        with mock.patch.object(server,'_notify_sse'),mock.patch.object(server.threading,'Thread'), \
                mock.patch.object(server.threading,'Timer'),mock.patch.object(server,'_PENDING_DISPATCH',{}):
            self.assertEqual(self.client.post('/alexa/queue_remove/',query_string={'key':server.API_KEY},json={'index':1,'video_id':b}).status_code,200)
            self.assertEqual(self.client.post('/alexa/state_event/',query_string={'key':server.API_KEY},json={
                'event':'finished','video_id':a,'token':'0|'+a+'|attempt'}).status_code,200)
            response=self.client.post('/alexa/state_event/',query_string={'key':server.API_KEY},json={
                'event':'started','video_id':b,'token':'1|'+b+'|q'+decision['decision_id']})
            self.assertTrue(response.json['correcting'])
            self.assertEqual(server._get_now_playing()['video_id'],c)
            self.assertEqual(server._PENDING_DISPATCH[self.SERIAL]['video_id'],c)

    def test_single_remaining_song_is_not_replaced_by_stale_skill_window(self):
        a,b,c=('A'*11,'B'*11,'C'*11)
        self._set_queue([_meta(b)],current_index=0)
        server._now_playing.update(video_id=b,queue_web_dirty=True,alexa_token='1|'+b+'|old',alexa_intent_at=10,playing=True)
        with mock.patch.object(server,'_notify_sse'),mock.patch.object(server,'_record_listen'), \
                mock.patch.object(server.threading,'Thread'),mock.patch.object(server,'_prewarm_queue_audio'):
            response=self.client.post('/alexa/state_event/',query_string={'key':server.API_KEY},json={
                'event':'started','video_id':b,'token':'1|'+b+'|resume','offset_in_ms':1234,
                'queue':[_meta(a),_meta(b),_meta(c)],'queue_index':1})
            self.assertEqual(response.status_code,200)
            self.assertEqual([x['video_id']for x in server._now_playing['queue']],[b])
            self.assertEqual(server._now_playing['queue_index'],0)

    def test_restart_without_decision_record_does_not_restore_deleted_successor(self):
        a,b,c=('A'*11,'B'*11,'C'*11)
        self._set_queue([_meta(a),_meta(c)])
        for key in ('alexa_token','alexa_intent_at','_alexa_pending','_alexa_finished_token'):
            server._now_playing.pop(key,None)
        server._now_playing.update(playing=False,playback_processing=False,preferred_output='alexa')
        with mock.patch.object(server,'_alexa_queue_decisions',{}),mock.patch.object(server,'_PENDING_DISPATCH',{}), \
                mock.patch.object(server.threading,'Timer'),mock.patch.object(server,'_notify_sse'):
            response=self.client.post('/alexa/state_event/',query_string={'key':server.API_KEY},json={
                'event':'started','video_id':b,'token':'1|'+b+'|q'+'a'*24,
                'queue':[_meta(a),_meta(b),_meta(c)],'queue_index':1})
            self.assertTrue(response.json['correcting'])
            self.assertEqual(server._now_playing['video_id'],c)
            self.assertEqual([x['video_id']for x in server._now_playing['queue']],[a,c])

    def test_restart_valid_queued_successor_commits_without_importing_old_window(self):
        a,b=('A'*11,'B'*11)
        self._set_queue([_meta(a),_meta(b)])
        for key in ('alexa_token','alexa_intent_at','_alexa_pending','_alexa_finished_token'):
            server._now_playing.pop(key,None)
        server._now_playing.update(playing=False,playback_processing=False,preferred_output='alexa')
        with mock.patch.object(server,'_alexa_queue_decisions',{}),mock.patch.object(server,'_notify_sse'),mock.patch.object(server,'_record_listen'):
            response=self.client.post('/alexa/state_event/',query_string={'key':server.API_KEY},json={
                'event':'started','video_id':b,'token':'1|'+b+'|q'+'b'*24,
                'queue':[_meta(a),_meta(b),_meta('C'*11)],'queue_index':1})
            self.assertEqual(response.status_code,200)
            self.assertTrue(server._now_playing['playing'])
            self.assertEqual(server._now_playing['video_id'],b)
            self.assertEqual([x['video_id']for x in server._now_playing['queue']],[a,b])

    def test_unknown_queue_token_cannot_interrupt_newer_manual_selection(self):
        a,b,c=('A'*11,'B'*11,'C'*11)
        self._set_queue([_meta(a),_meta(c)])
        server._now_playing.update(video_id=c,queue_index=1,playing=False,
            playback_processing=True,alexa_intent_at=123,alexa_token='0|'+a+'|old')
        with mock.patch.object(server,'_alexa_queue_decisions',{}):
            response=server._reconcile_buffered_alexa_start('1|'+b+'|q'+'a'*24)
            self.assertTrue(response['stop'])
            self.assertEqual(server._now_playing['video_id'],c)

    def test_superseded_correction_cannot_cancel_newer_pending_selection(self):
        newest_timer = mock.Mock()
        pending = {'timer':newest_timer,'video_id':'D','token':object()}
        old_timer = mock.Mock()
        def newer_selection(*args, **kwargs):
            server._now_playing.update(video_id='D',alexa_intent_at=11,playing=True)
            server._PENDING_DISPATCH[self.SERIAL] = pending
            return old_timer
        with mock.patch.object(server, '_PENDING_DISPATCH', {}), \
                mock.patch.object(server.threading, 'Timer', side_effect=newer_selection):
            server._schedule_play_dispatch(self.SERIAL,'A',expected_intent=10,expected_output='owner1')
            self.assertIs(server._PENDING_DISPATCH[self.SERIAL],pending)
            newest_timer.cancel.assert_not_called()
            old_timer.start.assert_not_called()

    def test_stale_buffer_correction_does_not_reenter_playback_lock(self):
        decision = self.decision()
        self._set_queue([_meta('A'), _meta('C'), _meta('B')])
        class ProductionLock:
            def __init__(self): self.lock = threading.Lock()
            def __enter__(self):
                if not self.lock.acquire(timeout=.2):
                    raise AssertionError('Buffered start correction re-entered playback lock')
                return self
            def __exit__(self, *args): self.lock.release()
        lock = ProductionLock()
        with mock.patch.object(server, '_np_lock', lock), mock.patch.object(server, '_PENDING_DISPATCH', {}), \
                mock.patch.object(server.threading, 'Timer') as timer:
            result = server._reconcile_buffered_alexa_start('1|B|q' + decision['decision_id'])
            self.assertTrue(result['correcting'])
            self.assertEqual(server._PENDING_DISPATCH[self.SERIAL]['video_id'], 'C')
            self.assertEqual(server._get_now_playing()['video_id'], 'C')
            timer.return_value.start.assert_called_once()

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

    def test_late_started_for_released_current_token_cannot_reclaim_phone(self):
        self.output['playback_output']='phone'
        with mock.patch.object(server,'_claim_alexa_output') as claim:
            response=self.client.post('/alexa/state_event/',json={'event':'started','video_id':'A','token':'0|A|attempt'},headers={'X-Api-Key':server.API_KEY})
        self.assertEqual(response.status_code,200)
        self.assertTrue(response.json.get('stop'))
        claim.assert_not_called()

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

    def test_finish_during_manual_next_does_not_cancel_pending_play_or_poison_watermark(self):
        server._now_playing.update(video_id='B',queue_index=1,playing=True,
            playback_processing=True,playback_confirmed=False,alexa_event_at=10)
        response=self.client.post('/alexa/state_event/',query_string={'key':server.API_KEY},json={
            'event':'finished','video_id':'A','token':'0|A|attempt','event_timestamp':'2026-10-25T00:00:00.000Z'})
        self.assertEqual(response.status_code,200)
        self.assertTrue(server._now_playing['playing'])
        self.assertTrue(server._now_playing['playback_processing'])
        self.assertEqual(server._now_playing['alexa_event_at'],10)

    def test_old_same_song_stop_during_seek_does_not_pause_seek(self):
        server._now_playing.update(playing=True,playback_processing=True,playback_confirmed=False,position_ms=179000)
        self.client.post('/alexa/state_event/',query_string={'key':server.API_KEY},json={
            'event':'stopped','video_id':'A','token':'0|A|attempt'})
        self.assertTrue(server._now_playing['playing'])
        self.assertTrue(server._now_playing['playback_processing'])

    def test_deleted_buffered_next_recovers_live_successor_if_echo_finishes_without_start(self):
        self.decision()
        self._set_queue([_meta('A'),_meta('C')])
        server._now_playing.update(playing=False,_alexa_finished_token='0|A|attempt')
        with mock.patch.object(server.time,'sleep'), mock.patch.object(server,'_promote_next_track',return_value=False), mock.patch.object(server,'_schedule_play_dispatch') as dispatch, mock.patch.object(server,'_notify_sse'):
            server._promote_after_finish('A')
        self.assertEqual(server._now_playing['video_id'],'C')
        self.assertTrue(server._now_playing['playing'])
        self.assertFalse(server._now_playing['playback_confirmed'])
        dispatch.assert_called_once()
        self.assertEqual(dispatch.call_args.args[:2],(self.SERIAL,'C'))

    def test_pause_during_finish_grace_cancels_recovery(self):
        server._now_playing.update(playing=False,_alexa_finished_token='0|A|attempt')
        def pause(_): server._now_playing.update(alexa_intent_at=11)
        with mock.patch.object(server.time,'sleep',side_effect=pause), mock.patch.object(server,'_schedule_play_dispatch') as dispatch:
            server._promote_after_finish('A')
        dispatch.assert_not_called()

    def test_finish_recovery_never_wraps_finite_queue_or_steals_phone_output(self):
        self._set_queue([_meta('A')])
        server._now_playing.update(playing=False,_alexa_finished_token='0|A|attempt')
        with mock.patch.object(server.time,'sleep'), mock.patch.object(server,'_promote_next_track',return_value=False), mock.patch.object(server,'_schedule_play_dispatch') as dispatch:
            server._promote_after_finish('A')
            self.output['playback_output']='phone'
            server._promote_after_finish('A')
        dispatch.assert_not_called()

    def test_finish_after_explicit_pause_cannot_restart_or_advance(self):
        server._now_playing.update(playing=False,alexa_intent_at=11)
        with mock.patch.object(server.threading,'Thread') as worker:
            self.client.post('/alexa/state_event/',query_string={'key':server.API_KEY},json={
                'event':'finished','video_id':'A','token':'0|A|attempt'})
        worker.assert_not_called()
        self.assertFalse(server._now_playing['playing'])

    def test_removed_prefetch_is_not_promoted_even_without_a_protocol2_decision(self):
        self._set_queue([_meta('A'),_meta('C')])
        server._now_playing.update(playing=False,queue_web_dirty=True)
        with mock.patch.object(server,'_prefetched_next',{'video_id':'B','at':server.time.time()}):
            self.assertFalse(server._promote_next_track('test'))
        self.assertEqual(server._now_playing['video_id'],'A')

    def test_valid_loop_prefetch_survives_a_dirty_queue(self):
        self._set_queue([_meta('A'),_meta('C')],current_index=1)
        server._now_playing.update(playing=False,queue_web_dirty=True,_alexa_pending={
            'current_video':'C','loop':True})
        with mock.patch.object(server,'_prefetched_next',{'video_id':'A','at':server.time.time()}), mock.patch.object(server,'_notify_sse'), mock.patch.object(server,'_record_listen'), mock.patch.object(server.threading,'Thread'):
            self.assertTrue(server._promote_next_track('test'))
        self.assertEqual(server._now_playing['video_id'],'A')
