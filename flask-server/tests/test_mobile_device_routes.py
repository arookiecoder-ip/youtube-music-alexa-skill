"""Exercise actual device routes with physical Echo/audio extraction stubbed."""
import ast
import json
from pathlib import Path
from unittest.mock import Mock
from test_output_routes import OutputRoutesTests
from test_app_queue import A, B, track
from mobile_devices import MobileDevices


class MobileDeviceRoutesTests(OutputRoutesTests):
    def setUp(self):
        super().setUp()
        self.registry = MobileDevices()
        self.namespace.update(_mobile_devices=self.registry, json=json)
        path = Path(__file__).resolve().parents[1] / 'server.py'
        nodes = [n for n in ast.parse(path.read_text()).body if isinstance(n, ast.FunctionDef)
                 and n.name in ('app_mobile_devices', '_pause_echo_for_mobile', '_reconcile_mobile_output', 'route_mobile_web_controls')]
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), self.namespace)

    def device(self, action, owner='phone-one', **extra):
        return self.client.post('/api/app/devices/', json={'action': action,
            'device_id': owner, 'session_id': 'session-' + owner, 'name': owner, **extra})

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
