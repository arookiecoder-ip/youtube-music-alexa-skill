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
