"""Exercise actual device routes with physical Echo/audio extraction stubbed."""
import ast
import json
from pathlib import Path
from unittest.mock import Mock
from test_output_routes import OutputRoutesTests
from mobile_devices import MobileDevices


class MobileDeviceRoutesTests(OutputRoutesTests):
    def setUp(self):
        super().setUp()
        self.registry = MobileDevices()
        self.namespace.update(_mobile_devices=self.registry, json=json)
        path = Path(__file__).resolve().parents[1] / 'server.py'
        nodes = [n for n in ast.parse(path.read_text()).body if isinstance(n, ast.FunctionDef)
                 and n.name in ('app_mobile_devices', '_pause_echo_for_mobile')]
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
