"""Exercise actual device routes with physical Echo/audio extraction stubbed."""
import ast
import json
from pathlib import Path
from unittest.mock import Mock
from test_output_routes import OutputRoutesTests
from test_app_queue import A, B, track
from mobile_devices import MobileDevices


class MobileDeviceRoutesTests(OutputRoutesTests):
    def test_last_track_clear_requires_current_phone_ownership(self):
        claim = self.output_request('claim').json
        self.state.update(queue=[track(A)], video_id=A, playing=True)
        request = {'action': 'clear', 'output_owner': 'phone-one', 'output_token': 'stale'}
        self.assertEqual(self.client.post('/api/app/queue/', json=request).status_code, 409)
        self.assertEqual(self.state['video_id'], A)
        request['output_token'] = claim['output_token']
        self.assertEqual(self.client.post('/api/app/queue/', json=request).status_code, 200)
        self.assertEqual(self.state['queue'], [])
        self.assertFalse(self.state['playing'])
        self.assertEqual(self.state['video_id'], '')

    def setUp(self):
        super().setUp()
        self.registry = MobileDevices()
        self.namespace.update(_mobile_devices=self.registry, json=json)
        path = Path(__file__).resolve().parents[1] / 'server.py'
        nodes = [n for n in ast.parse(path.read_text()).body if isinstance(n, ast.FunctionDef)
                 and n.name in ('app_mobile_devices', '_mobile_handoff_snapshot', '_pause_echo_for_mobile', '_reconcile_mobile_output', 'route_mobile_web_controls')]
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), self.namespace)

    def device(self, action, owner='phone-one', **extra):
        return self.client.post('/api/app/devices/', json={'action': action,
            'device_id': owner, 'session_id': 'session-' + owner, 'name': owner, **extra})

    def test_remote_seek_preserves_mobile_track_identity_and_rejects_stale_cursor(self):
        claim = self.output_request('claim').json
        self.device('online')
        self.state.update(video_id=A, queue_index=0, queue=[dict(track(A), entry_id='entry-a'), track(B)],
                          playback_instance='play-a')
        request = dict(serial='mobile:phone-one', output_token=claim['output_token'],
                       video_id=A, expected_queue_index=0, position_ms=12000)
        response = self.client.post('/alexa/seek/', json=request)
        self.assertEqual(response.status_code, 200)
        command = self.device('online').json['commands'][0]
        self.assertEqual(command['action'], 'seek')
        self.assertEqual(command['payload']['video_id'], A)
        self.assertEqual(command['payload']['expected_queue_index'], 0)
        self.assertEqual(command['payload']['expected_entry_id'], 'entry-a')
        self.assertEqual(command['payload']['expected_playback_instance'], 'play-a')
        self.state.update(video_id=B, queue_index=1)
        self.assertEqual(self.client.post('/alexa/seek/', json=request).status_code, 409)
        self.assertEqual(len(self.device('online').json['commands']), 1)

    def test_legacy_remote_seek_is_bound_before_delivery_and_invalid_positions_fail(self):
        claim = self.output_request('claim').json
        self.device('online')
        self.state.update(video_id=A, queue_index=0, queue=[track(A)])
        request = dict(serial='mobile:phone-one', output_token=claim['output_token'], position_ms=12000)
        self.assertEqual(self.client.post('/alexa/seek/', json=request).status_code, 200)
        self.assertEqual(self.device('online').json['commands'][0]['payload']['video_id'], A)
        for position in [True, 'inf', 'nan']:
            self.assertEqual(self.client.post('/alexa/seek/', json=dict(request, position_ms=position)).status_code, 400)
        self.state.update(video_id=A, queue_index=1, queue=[track(A), track(A)])
        self.assertEqual(self.client.post('/alexa/seek/', json=dict(request, video_id=A, expected_queue_index=0)).status_code, 409)

    def test_wait_budget_is_opt_in_bounded_and_compatible_with_old_clients(self):
        self.registry.wait = Mock()
        token = self.output.snapshot()['output_token']
        for extra, expected in (({}, 2), ({'wait_seconds': 8}, 8), ({'wait_seconds': 100}, 8), ({'wait_seconds': 'bad'}, 2)):
            reply = self.device('online', wait=True, output_token=token, **extra)
            self.assertEqual(reply.status_code, 200)
            self.assertEqual(self.registry.wait.call_args.args[1], expected)
            self.assertEqual(reply.json["presence_wait_seconds"], 8)

    def test_online_listing_and_close_stop_phone_state_without_echo_command(self):
        claim = self.output_request('claim').json
        self.device('online')
        self.assertEqual(self.client.get('/api/app/devices/').json['devices'][0]['id'], 'phone-one')
        self.state['playing'] = True
        self.command.reset_mock()
        self.assertEqual(self.device('offline').status_code, 200)
        self.assertEqual(self.client.get('/api/app/devices/').json['devices'], [])
        self.assertFalse(self.output.owns_phone('phone-one', claim['output_token']))
        self.assertFalse(self.state['playing'])
        self.command.assert_not_called()

    def test_closed_alexa_controller_does_not_pause_echo(self):
        self.device('online')
        self.command.reset_mock()
        self.device('offline')
        self.assertTrue(self.state['playing'])
        self.command.assert_not_called()

    def test_new_destination_gets_queue_in_transfer_and_presence_but_not_every_heartbeat(self):
        self.device('online', owner='phone-two')
        initial = self.output.snapshot()
        self.state.update(queue=[track(A), track(B)], queue_index=1, video_id=B, position_ms=42000, playing=True)
        transferred = self.device('transfer', target_id='phone-two', output_token=initial['output_token'], serial='echo-one', include_state=True)
        self.assertEqual(transferred.status_code, 200)
        self.assertEqual(transferred.json['now_playing']['position_ms'], 42000)
        self.assertEqual(transferred.json['now_playing']['video_id'], B)
        self.assertTrue(transferred.json['now_playing']['playing'])
        token = transferred.json['output_token']
        receiver = self.device('online', owner='phone-two', include_handoff_state=True, accepted_token='old')
        self.assertEqual(receiver.json['now_playing']['queue'], self.state['queue'])
        settled = self.device('online', owner='phone-two', include_handoff_state=True, accepted_token=token)
        self.assertNotIn('now_playing', settled.json)
        controller = self.device('online', include_handoff_state=True, accepted_token='old')
        self.assertNotIn('now_playing', controller.json)

    def test_snapshot_cannot_be_read_for_a_superseded_or_unacknowledged_transfer(self):
        from playback_output import OutputConflict
        claim = self.output_request('claim').json
        with self.assertRaises(OutputConflict):
            self.namespace['_mobile_handoff_snapshot']('old')
        with self.assertRaises(OutputConflict):
            self.output.transfer_phone('phone-two', claim['output_token'], lambda: None, timeout=.01)
        pending = self.output.snapshot()
        with self.assertRaises(OutputConflict):
            self.namespace['_mobile_handoff_snapshot'](pending['output_token'])

    def test_output_change_during_snapshot_copy_is_rejected(self):
        from playback_output import OutputConflict
        claim = self.output_request('claim').json
        def changed_during_copy(serial):
            self.output.phone('phone-one', lambda: None)
            return dict(self.state)
        self.namespace['_np_snapshot'] = changed_during_copy
        with self.assertRaises(OutputConflict):
            self.namespace['_mobile_handoff_snapshot'](claim['output_token'])

    def test_expired_source_ack_cannot_jump_the_destination_cursor(self):
        from playback_output import OutputConflict
        claim = self.output_request('claim').json
        with self.assertRaises(OutputConflict):
            self.output.transfer_phone('phone-two', claim['output_token'], lambda: None, timeout=.01)
        pending = self.output.snapshot()
        self.state['position_ms'] = 42000
        self.output.clock = lambda: self.output.pending_until + 1
        response = self.client.post('/api/app/output/', json={'action': 'ack', 'output_owner': 'phone-one',
            'output_token': pending['output_token'], 'position_ms': 1000, 'playing': False})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.state['position_ms'], 42000)

    def test_paused_transfer_snapshot_does_not_request_playback(self):
        self.device('online', owner='phone-two')
        self.state['playing'] = False
        self.state['playback_processing'] = False
        before = self.output.snapshot()
        self.command.reset_mock()
        response = self.device('transfer', target_id='phone-two', output_token=before['output_token'], include_state=True)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json['now_playing']['playing'])
        self.command.assert_not_called()

    def test_offline_target_and_stale_transfer_leave_current_owner_unchanged(self):
        claim = self.output_request('claim').json
        result = self.device('transfer', target_id='offline', output_token=claim['output_token'])
        self.assertEqual(result.status_code, 400)
        self.device('online', owner='phone-two')
        result = self.device('transfer', target_id='phone-two', output_token='old-token')
        self.assertEqual(result.status_code, 409)
        self.assertTrue(self.output.owns_phone('phone-one', claim['output_token']))

    def test_remote_commands_require_live_target_and_current_token(self):
        self.device('online')
        claim = self.output_request('claim').json
        self.assertEqual(self.device('command', owner='controller', target_id='phone-one',
            output_token='stale', command='next').status_code, 409)
        self.assertEqual(self.device('command', owner='controller', target_id='phone-one',
            output_token=claim['output_token'], command='next', command_id='next-1').status_code, 200)
        reply = self.device('online').json
        self.assertEqual(reply['commands'][0]['action'], 'next')
        self.assertEqual(self.device('online', ack=['next-1']).json['commands'], [])

    def test_transfer_ack_commits_actual_source_position_and_pause_state(self):
        first = self.output_request('claim').json
        with self.assertRaises(self.namespace['OutputConflict']):
            self.output.transfer_phone('phone-two', first['output_token'], lambda: None, timeout=0.01)
        pending = self.output.snapshot()
        response = self.client.post('/api/app/output/', json={'action': 'ack', 'output_owner': 'phone-one',
            'output_token': pending['output_token'], 'position_ms': 42000, 'playing': False})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.state['position_ms'], 42000)
        self.assertFalse(self.state['playing'])
        self.assertTrue(self.output.owns_phone('phone-two', pending['output_token']))

    def test_queue_tools_are_delivered_to_owner_without_alexa_dispatch(self):
        self.device('online')
        claim = self.output_request('claim').json
        self.command.reset_mock()
        response = self.device('command', owner='controller', target_id='phone-one',
            output_token=claim['output_token'], command='tool', payload={'tool': 'CLEAR_PLAYED'})
        self.assertEqual(response.status_code, 200)
        queued = self.device('online').json['commands'][0]
        self.assertEqual(queued['action'], 'tool')
        self.assertEqual(queued['payload']['tool'], 'CLEAR_PLAYED')
        self.command.assert_not_called()

    def test_alexa_to_remote_phone_preserves_active_playback_without_starting_echo(self):
        self.device('online', owner='phone-one')
        self.device('online', owner='phone-two')
        self.state['playing'] = True
        initial = self.output.snapshot()
        response = self.device('transfer', target_id='phone-two', output_token=initial['output_token'], serial='echo-one')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(self.state['playing'])
        self.assertFalse(self.state['playback_processing'])
        self.assertEqual(response.json['output_controller'], 'phone-one')
        self.command.assert_called_once_with('echo-one', 'pause')

    def test_closing_controlled_phone_pauses_without_starting_controller(self):
        self.test_alexa_to_remote_phone_preserves_active_playback_without_starting_echo()
        old = self.output.snapshot()
        self.command.reset_mock()
        self.device('offline', owner='phone-two')
        latest = self.output.snapshot()
        self.assertEqual(latest['output_owner'], '')
        self.assertFalse(self.state['playing'])
        self.assertEqual(self.state['playback_error']['type'], 'device_offline')
        self.command.assert_not_called()

    def test_expired_controlled_phone_never_auto_starts_the_controller(self):
        self.test_alexa_to_remote_phone_preserves_active_playback_without_starting_echo()
        now = [100.0]
        self.registry.clock = self.output.clock = lambda: now[0]
        self.output.lease_until = 112
        self.registry.devices['phone-one']['until'] = 200
        self.registry.devices['phone-two']['until'] = 104
        now[0] = 105
        self.client.get('/api/app/devices/')
        self.assertEqual(self.output.snapshot()['output_owner'], 'phone-two')
        now[0] = 113
        self.client.get('/api/app/devices/')
        self.assertEqual(self.output.snapshot()['output_owner'], 'phone-two')
        self.assertFalse(self.state['playing'])
        self.assertIn('Choose an online device', self.state['playback_error']['message'])

    def test_disconnected_target_reports_once_even_after_error_is_consumed(self):
        self.test_alexa_to_remote_phone_preserves_active_playback_without_starting_echo()
        self.registry.offline('phone-two', 'session-phone-two')
        self.output.lease_until = self.output.clock() - 1
        notify = Mock()
        self.namespace['_notify_sse'] = notify
        self.namespace['_reconcile_mobile_output']()
        self.state['playback_error'] = None  # Snapshot errors are consumed once.
        self.namespace['_reconcile_mobile_output']()
        notify.assert_called_once()
        self.assertFalse(self.state['playing'])
        self.assertEqual(self.output.snapshot()['output_owner'], 'phone-two')

    def test_closed_controller_does_not_steal_playback_back_from_target(self):
        self.test_alexa_to_remote_phone_preserves_active_playback_without_starting_echo()
        self.device('offline', owner='phone-one')
        self.assertEqual(self.output.snapshot()['output_owner'], 'phone-two')
        self.device('offline', owner='phone-two')
        self.assertEqual(self.output.snapshot()['output_owner'], '')

    def test_remote_volume_targets_only_the_active_phone_and_rejects_stale_session(self):
        self.device('online', volume=20)
        claim = self.output_request('claim').json
        self.command.reset_mock()
        response = self.device('command', owner='controller', target_id='phone-one',
            output_token=claim['output_token'], command='volume', payload={'value': 72})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.device('online').json['commands'][0]['payload'], {'value': 72})
        self.assertEqual(self.device('command', owner='controller', target_id='phone-one',
            output_token='old', command='volume', payload={'value': 72}).status_code, 409)
        self.assertEqual(self.device('command', target_id='phone-one', output_token=claim['output_token'],
            command='volume', payload={'value': 101}).status_code, 400)
        self.command.assert_not_called()

    def test_web_mobile_controls_do_not_dispatch_or_claim_alexa(self):
        self.device('online', volume=32)
        claim = self.output_request('claim').json
        self.command.reset_mock()
        # These are the webapp's existing routes, using the selected output token.
        response = self.client.post('/alexa/command/', json={'serial': 'mobile:phone-one',
            'action': 'next', 'output_token': claim['output_token']})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.device('online').json['commands'][0]['action'], 'next')
        self.assertEqual(self.client.get('/alexa/volume/?serial=mobile:phone-one').json['volume'], 32)
        stale = self.client.post('/alexa/command/', json={'serial': 'mobile:phone-one',
            'action': 'pause', 'output_token': 'stale'})
        self.assertEqual(stale.status_code, 409)
        self.assertTrue(self.output.owns_phone('phone-one', claim['output_token']))
        self.command.assert_not_called()

    def test_web_seek_and_large_playlist_are_delivered_to_phone(self):
        self.device('online')
        claim = self.output_request('claim').json
        for path, body in [('/alexa/seek/', {'position_seconds': 4.5}),
                           ('/alexa/play_queue/', {'playlist_id': 'PL1000', 'shuffle': True})]:
            response = self.client.post(path, json={'serial': 'mobile:phone-one', 'output_token': claim['output_token'], **body})
            self.assertEqual(response.status_code, 200)
        commands = self.device('online').json['commands']
        self.assertEqual(commands[0]['payload']['position_ms'], 4500)
        self.assertEqual(commands[1]['payload']['playlist_id'], 'PL1000')
        self.assertTrue(commands[1]['payload']['shuffle'])

    def test_handoff_claim_returns_the_latest_queue_cursor_and_original_play_state(self):
        self.state.update(playing=True, video_id=A, position_ms=42000, queue=[track(A), track(B)], queue_index=0)
        response = self.client.post('/api/app/output/', json={'action': 'claim', 'output_owner': 'phone-one',
            'serial': 'echo-one', 'include_state': True})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['now_playing']['video_id'], A)
        self.assertEqual(response.json['now_playing']['position_ms'], 42000)
        self.assertEqual(len(response.json['now_playing']['queue']), 2)
        self.assertTrue(response.json['now_playing']['playing'])
        self.assertFalse(self.state['playing'])
        self.assertTrue(self.output.owns_phone('phone-one', response.json['output_token']))

    def test_foreground_after_target_closed_requires_an_explicit_resume(self):
        self.test_alexa_to_remote_phone_preserves_active_playback_without_starting_echo()
        self.device('offline', owner='phone-two')
        self.command.reset_mock()
        queue = list(self.state['queue'])
        reply = self.device('foreground').json
        self.assertFalse(reply['default_selected'])
        self.assertEqual(reply['output_owner'], '')
        self.assertFalse(self.state['playing'])
        self.assertEqual(self.state['queue'], queue)
        explicit = self.device('resume', output_token=reply['output_token']).json
        self.assertTrue(explicit['default_selected'])
        self.assertEqual(explicit['output_owner'], 'phone-one')
        self.assertNotIn('playback_error', self.state)
        self.command.assert_not_called()

    def test_foreground_preserves_playing_and_buffering_devices(self):
        self.test_alexa_to_remote_phone_preserves_active_playback_without_starting_echo()
        token = self.output.snapshot()['output_token']
        for playing, buffering in ((True, False), (False, True)):
            self.state.update(playing=playing, playback_processing=buffering)
            reply = self.device('foreground').json
            self.assertFalse(reply['default_selected'])
            self.assertEqual(reply['output_owner'], 'phone-two')
            self.assertEqual(reply['output_token'], token)

    def test_paused_online_phone_is_preserved_even_after_idle_timeout(self):
        import time
        self.test_alexa_to_remote_phone_preserves_active_playback_without_starting_echo()
        self.state.update(playing=False, playback_processing=False, _last_playback_activity_at=time.time())
        self.assertFalse(self.device('foreground').json['default_selected'])
        self.state['_last_playback_activity_at'] = time.time() - 31
        reply = self.device('foreground').json
        self.assertFalse(reply['default_selected'])
        self.assertEqual(reply['output_owner'], 'phone-two')
        self.assertFalse(self.state['playing'])

    def test_foreground_preserves_paused_alexa(self):
        self.device('online')
        self.assertFalse(self.device('foreground').json['default_selected'])
        self.state.update(playing=False, playback_processing=False, _last_playback_activity_at=0)
        self.command.reset_mock()
        reply = self.device('foreground').json
        self.assertFalse(reply['default_selected'])
        self.assertEqual(reply['playback_output'], 'alexa')
        self.command.assert_not_called()

    def test_repeated_idle_foreground_does_not_invalidate_current_phone_token(self):
        self.state.update(video_id='', queue=[])
        self.state.update(playing=False, playback_processing=False, _last_playback_activity_at=0)
        first = self.device('foreground').json
        second = self.device('foreground').json
        self.assertTrue(second['default_selected'])
        self.assertEqual(first['output_token'], second['output_token'])

    def test_idle_selection_rejects_stale_handoff_token(self):
        old = self.output.snapshot()['output_token']
        self.output.select_idle_phone('phone-one', old)
        with self.assertRaises(self.namespace['OutputConflict']):
            self.output.select_idle_phone('phone-two', old)
        self.assertEqual(self.output.snapshot()['output_owner'], 'phone-one')

    def test_online_route_exposes_actual_target_volume_levels(self):
        reply = self.device('online', volume=66, volume_steps=15).json
        device = next(d for d in reply['devices'] if d['id'] == 'phone-one')
        self.assertEqual(device['volume'], 66)
        self.assertEqual(device['volume_steps'], 15)

    def test_resume_after_local_owner_closes_selects_controller_without_autoplay(self):
        self.device('online')
        self.device('online', owner='phone-two')
        self.output_request('claim')
        self.state.update(playing=True, position_ms=42000)
        self.device('offline')
        closed = self.output.snapshot()
        self.assertFalse(self.state['playing'])
        self.command.reset_mock()
        reply = self.device('resume', owner='phone-two', output_token=closed['output_token'])
        self.assertEqual(reply.status_code, 200)
        self.assertTrue(reply.json['default_selected'])
        self.assertEqual(reply.json['output_owner'], 'phone-two')
        self.assertEqual(reply.json['now_playing']['position_ms'], 42000)
        self.assertFalse(self.state['playing'])  # Only the client starts audible audio.
        self.command.assert_not_called()

    def test_resume_live_target_delivers_play_without_selecting_controller(self):
        self.device('online')
        self.device('online', owner='phone-two')
        claim = self.output_request('claim').json
        self.state['playing'] = False
        reply = self.device('resume', owner='phone-two', output_token=claim['output_token'], command_id='resume-live')
        self.assertEqual(reply.status_code, 200)
        self.assertFalse(reply.json['default_selected'])
        self.assertEqual(self.output.snapshot()['output_owner'], 'phone-one')
        commands = self.device('online').json['commands']
        self.assertEqual(commands[0]['action'], 'play')

    def test_resume_stale_token_cannot_steal_new_output(self):
        self.device('online')
        claim = self.output_request('claim').json
        self.device('offline')
        reply = self.device('resume', owner='phone-two', output_token=claim['output_token'])
        self.assertEqual(reply.status_code, 409)
        self.assertEqual(self.output.snapshot()['output_owner'], '')

    def test_expired_local_owner_presence_freezes_playback_without_controller(self):
        self.device('online')
        self.output_request('claim')
        self.state['playing'] = True
        self.registry.offline('phone-one', 'session-phone-one')
        self.output.lease_until = self.output.clock() - 1
        self.client.get('/api/app/devices/')
        self.assertFalse(self.state['playing'])
        self.assertEqual(self.state['playback_error']['type'], 'device_offline')
        reply = self.device('resume', owner='phone-two', output_token=self.output.snapshot()['output_token'])
        self.assertEqual(reply.status_code, 200)
        self.assertEqual(reply.json['output_owner'], 'phone-two')

    def test_missing_presence_with_live_lease_cannot_start_second_phone(self):
        self.device('online')
        claim = self.output_request('claim').json
        self.registry.offline('phone-one', 'session-phone-one')
        self.state['playing'] = True
        reply = self.device('resume', owner='phone-two', output_token=claim['output_token'])
        self.assertEqual(reply.status_code, 409)
        self.assertTrue(self.state['playing'])
        self.assertEqual(self.output.snapshot()['output_owner'], 'phone-one')

    def test_ack_delayed_past_source_lease_cannot_rewind_target_playback(self):
        now = [100.0]
        self.output.clock = lambda: now[0]
        self.device('online')
        self.device('online', owner='phone-two')
        claim = self.output_request('claim').json
        with self.assertRaises(self.namespace['OutputConflict']):
            self.output.transfer_phone('phone-two', claim['output_token'], lambda: None, timeout=0)
        transfer = self.output.snapshot()
        now[0] = 113.0
        self.state.update(playing=True, position_ms=73000)
        reply = self.client.post('/api/app/output/', json={'action': 'ack',
            'output_owner': 'phone-one', 'output_token': transfer['output_token'],
            'position_ms': 42000, 'playing': True})
        self.assertEqual(reply.status_code, 409)
        self.assertEqual(self.state['position_ms'], 73000)
        self.assertTrue(self.state['playing'])
        self.assertTrue(self.output.owns_phone('phone-two', transfer['output_token']))

    def test_controlled_phone_presence_gap_with_live_lease_is_not_disconnect(self):
        self.test_alexa_to_remote_phone_preserves_active_playback_without_starting_echo()
        self.registry.devices.pop('phone-two')
        self.namespace['_reconcile_mobile_output']()
        self.assertTrue(self.state['playing'])
        self.assertFalse(self.state.get('playback_error'))

    def test_returning_presence_clears_only_its_offline_error_and_retained_event(self):
        self.test_alexa_to_remote_phone_preserves_active_playback_without_starting_echo()
        error = {'type': 'device_offline', 'offline_owner': 'phone-two'}
        self.state.update(playback_error=error, _playback_error_event={'source': error})
        self.device('online', owner='phone-two')
        self.assertNotIn('playback_error', self.state)
        self.assertNotIn('_playback_error_event', self.state)
        self.state['playback_error'] = {'type': 'dispatch_error'}
        self.device('online', owner='phone-two')
        self.assertEqual(self.state['playback_error']['type'], 'dispatch_error')

    def test_empty_idle_selection_does_not_raise_disconnect_error(self):
        self.output.select_idle_phone('phone-one', self.output.snapshot()['output_token'])
        self.output.lease_until = self.output.clock() - 1
        self.state.update(video_id='', playing=False, playback_processing=False)
        self.namespace['_reconcile_mobile_output']()
        self.assertFalse(self.state.get('playback_error'))

    def test_already_closed_output_does_not_create_a_second_disconnect_event(self):
        self.test_alexa_to_remote_phone_preserves_active_playback_without_starting_echo()
        self.device('offline', owner='phone-two')
        self.state['playback_error'] = None
        self.namespace['_reconcile_mobile_output']()
        self.assertFalse(self.state.get('playback_error'))

    def test_browser_wait_slot_exhaustion_returns_bounded_retry_without_blocking(self):
        token = self.output.snapshot()['output_token']
        self.registry.wait_bounded = Mock(return_value=False)
        reply = self.device('online', wait=True, bounded_wait=True, wait_seconds=2, output_token=token)
        self.assertEqual(reply.status_code, 200)
        self.assertEqual(reply.json['presence_retry_ms'], 750)
        self.registry.wait_bounded.assert_called_once()

    def test_browser_wait_is_immediate_on_revision_change_and_preserves_busy_commands(self):
        self.device('online')
        claim = self.output_request('claim').json
        self.registry.command('phone-one', claim['output_token'], 'seek', {'position_ms': 1000}, 'busy')
        self.registry.wait_bounded = Mock(return_value=True)
        reply = self.device('online', wait=True, bounded_wait=True, wait_seconds=2,
                            output_token=claim['output_token'], busy_commands=['busy'])
        self.registry.wait_bounded.assert_called_once()
        self.assertEqual(reply.json['presence_retry_ms'], 50)
        self.assertEqual(reply.json['commands'][0]['id'], 'busy')

    def fast_handoff_setup(self):
        self.device('online')
        claim = self.output_request('claim').json
        self.state.update(video_id=A, queue_index=0, queue=[dict(track(A), entry_id='entry-a'), track(B)])
        dispatch = Mock()
        self.namespace.update(_schedule_play_dispatch=dispatch, _update_now_playing=lambda **kw: self.state.update(kw))
        return claim, dispatch

    def test_paused_source_can_install_cursor_and_start_echo_in_one_request(self):
        claim, dispatch = self.fast_handoff_setup()
        reply = self.device('alexa_handoff', serial='echo-two', output_token=claim['output_token'],
            source_paused=True, position_ms=12345, playing=True, video_id=A, current_entry_id='entry-a')
        self.assertEqual(reply.status_code, 200)
        self.assertFalse(reply.json['handoff_pending'])
        self.assertEqual(self.state['position_ms'], 12345)
        self.assertTrue(self.state['playing'])
        dispatch.assert_called_once_with('echo-two', A, 12345, delay=0)

    def test_paused_handoff_stays_paused_and_never_dispatches_echo_play(self):
        claim, dispatch = self.fast_handoff_setup()
        reply = self.device('alexa_handoff', serial='echo-two', output_token=claim['output_token'],
            source_paused=True, position_ms=12345, playing=False, video_id=A, current_entry_id='entry-a')
        self.assertEqual(reply.status_code, 200)
        self.assertFalse(self.state['playing'])
        self.assertEqual(self.state['position_ms'], 12345)
        dispatch.assert_not_called()

    def test_fast_handoff_rejects_old_tokens_wrong_owners_and_old_entries_without_switching(self):
        claim, dispatch = self.fast_handoff_setup()
        valid = dict(serial='echo-two', output_token=claim['output_token'], source_paused=True,
            position_ms=1000, playing=True, video_id=A, current_entry_id='entry-a')
        for owner, changes in [('phone-two', {}), ('phone-one', {'output_token':'old'}),
            ('phone-one', {'source_paused':False}), ('phone-one', {'current_entry_id':'old'}),
            ('phone-one', {'video_id':B}), ('phone-one', {'position_ms':True})]:
            reply = self.device('alexa_handoff', owner=owner, **dict(valid, **changes))
            self.assertIn(reply.status_code, (400, 409))
            self.assertEqual(self.output.snapshot()['output_token'], claim['output_token'])
        dispatch.assert_not_called()

    def test_browser_refresh_closes_silently_and_no_other_foreground_device_takes_the_queue(self):
        self.device('online', owner='browser-one')
        claim=self.client.post('/api/app/output/', json={'action':'claim','output_owner':'browser-one','serial':'echo-one'}).json
        self.state.update(video_id=A, playing=True, position_ms=5000)
        reply=self.device('offline', owner='browser-one', reason='pagehide', output_token=claim['output_token'], position_ms=8000)
        self.assertEqual(reply.status_code,200)
        self.assertFalse(self.state['playing'])
        self.assertEqual(self.state['position_ms'],8000)
        self.assertFalse(self.state.get('playback_error'))
        foreground=self.device('foreground', owner='phone-two').json
        self.assertFalse(foreground['default_selected'])
        self.assertEqual(self.output.snapshot()['output_owner'],'')
        resumed=self.device('resume', owner='phone-two', output_token=self.output.snapshot()['output_token']).json
        self.assertTrue(resumed['default_selected'])
        self.assertEqual(self.output.snapshot()['output_owner'],'phone-two')

    def test_stale_unload_cannot_close_a_new_browser_presence_session(self):
        self.device('online', owner='browser-one')
        self.output.phone('browser-one', lambda:None)
        self.state.update(playing=True)
        self.device('online', owner='browser-one', session_id='new-session')
        reply=self.device('offline', owner='browser-one', reason='pagehide', output_token=self.output.snapshot()['output_token'])
        self.assertEqual(reply.status_code, 200)
        self.assertEqual(self.output.snapshot()['output_owner'], 'browser-one')
        self.assertTrue(self.state['playing'])

    def test_foreground_cannot_claim_a_saved_queue_with_no_current_video(self):
        self.state.update(video_id='', playing=False, playback_processing=False)
        self.assertFalse(self.device('foreground').json['default_selected'])

    def test_closed_browser_fails_over_to_current_foreground_phone_with_exact_cursor(self):
        self.device('online', owner='browser-one')
        self.device('online', owner='phone-two', foreground=True)
        claim=self.output.phone('browser-one', lambda:None)
        self.state.update(video_id=A, playing=True, position_ms=5000)
        result=self.device('offline', owner='browser-one', reason='pagehide', output_token=claim['output_token'], position_ms=8123)
        self.assertEqual(result.status_code,200)
        self.assertEqual(self.output.snapshot()['output_owner'],'phone-two')
        self.assertTrue(self.state['playing'])
        self.assertEqual(self.state['position_ms'],8123)
        self.assertFalse(self.state.get('playback_error'))
        self.assertFalse(self.output.owns_phone('browser-one', claim['output_token']))
        grant=self.device('online', owner='phone-two', include_handoff_state=True, accepted_token='').json
        self.assertEqual(grant['now_playing']['video_id'],A)
        self.assertTrue(grant['now_playing']['playing'])

    def test_closed_paused_browser_selects_current_phone_without_autoplay(self):
        self.device('online', owner='browser-one')
        self.device('online', owner='phone-two', foreground=True)
        claim=self.output.phone('browser-one', lambda:None)
        self.state.update(video_id=A, playing=False, playback_processing=False)
        self.device('offline', owner='browser-one', reason='pagehide', output_token=claim['output_token'])
        self.assertEqual(self.output.snapshot()['output_owner'],'phone-two')
        self.assertFalse(self.state['playing'])
        self.assertFalse(self.state['playback_processing'])

    def test_unconfirmed_network_absence_cannot_grant_two_audible_owners(self):
        self.device('online', owner='browser-one')
        self.device('online', owner='phone-two', foreground=True)
        claim=self.output.phone('browser-one', lambda:None)
        self.registry.offline('browser-one','session-browser-one')
        self.namespace['_reconcile_mobile_output']()
        self.assertEqual(self.output.snapshot()['output_owner'],'browser-one')
        self.output.lease_until=0
        self.namespace['_reconcile_mobile_output']()
        self.assertEqual(self.output.snapshot()['output_owner'],'phone-two')
        self.assertFalse(self.output.owns_phone('browser-one',claim['output_token']))

    def test_offline_echo_selects_current_phone_only_after_server_verifies_presence(self):
        self.output.serial='echo-one'
        self.command.reset_mock()
        self.namespace['alexa_remote'].remote.devices=Mock(return_value=([{'serial':'echo-one','online':False}],None))
        result=self.device('foreground',offline_alexa='echo-one').json
        self.assertTrue(result['default_selected'])
        self.assertEqual(result['output_owner'],'phone-one')
        self.assertFalse(self.state['playing'])
        self.command.assert_not_called()

    def test_online_echo_or_presence_error_cannot_be_claimed_as_offline(self):
        self.output.serial='echo-one'
        for response in [([{'serial':'echo-one','online':True}],None),(None,'network error')]:
            self.namespace['alexa_remote'].remote.devices=Mock(return_value=response)
            result=self.device('foreground',offline_alexa='echo-one').json
            self.assertFalse(result['default_selected'])
            self.assertEqual(result['playback_output'],'alexa')

    def test_confirmed_offline_echo_does_not_block_explicit_phone_play_with_a_pause_error(self):
        self.output.serial='echo-one'
        self.state.update(video_id=A,playing=True)
        self.namespace['alexa_remote'].remote.devices=Mock(return_value=([{'serial':'echo-one','online':False}],None))
        self.command.return_value='offline'
        self.command.side_effect=None
        self.namespace['_pause_echo_for_mobile']('echo-one')
        self.command.assert_not_called()
        self.assertFalse(self.state['playing'])

    def test_idle_closed_browser_still_selects_an_open_foreground_phone(self):
        self.device('online',owner='browser-one')
        self.device('online',owner='phone-two',foreground=True)
        claim=self.output.phone('browser-one',lambda:None)
        self.state.update(video_id='',queue=[],playing=False,playback_processing=False)
        self.device('offline',owner='browser-one',reason='pagehide',output_token=claim['output_token'])
        self.assertEqual(self.output.snapshot()['output_owner'],'phone-two')
        self.assertFalse(self.state['playing'])

    def test_closing_a_buffering_browser_preserves_its_play_intent(self):
        self.device('online',owner='browser-one')
        self.device('online',owner='phone-two',foreground=True)
        claim=self.output.phone('browser-one',lambda:None)
        self.state.update(video_id=A,playing=False,playback_processing=True,playback_confirmed=False)
        self.device('offline',owner='browser-one',reason='pagehide',output_token=claim['output_token'])
        self.assertEqual(self.output.snapshot()['output_owner'],'phone-two')
        self.assertTrue(self.state['playing'])
        self.assertTrue(self.state['playback_processing'])

    def test_abrupt_loss_recovers_last_reported_cursor_instead_of_skipping_the_outage(self):
        self.device('online',owner='browser-one')
        self.device('online',owner='phone-two',foreground=True)
        claim=self.output.phone('browser-one',lambda:None)
        self.state.update(video_id=A,playing=True,position_ms=27000,
            _phone_report_cursor=(claim['output_token'],'browser-one',12345))
        self.registry.offline('browser-one','session-browser-one')
        self.output.lease_until=0
        self.namespace['_reconcile_mobile_output']()
        self.assertEqual(self.output.snapshot()['output_owner'],'phone-two')
        self.assertEqual(self.state['position_ms'],12345)

    def test_offline_echo_with_missing_server_serial_does_not_strand_a_saved_queue(self):
        self.output.serial=''
        self.namespace['alexa_remote'].remote.devices=Mock(return_value=([{'serial':'echo-one','online':False}],None))
        result=self.device('foreground',offline_alexa='echo-one').json
        self.assertTrue(result['default_selected'])
        self.assertEqual(result['output_owner'],'phone-one')
        self.assertTrue(self.state['queue'])
        self.assertFalse(self.state['playing'])
