import importlib.util
from pathlib import Path
import unittest
from unittest import mock
spec = importlib.util.spec_from_file_location('failover', Path(__file__).parents[1] / 'musicbox-tunnel-failover.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

class FailoverTests(unittest.TestCase):
    def test_three_failures_required(self):
        state = {'mode': 'tunnel'}
        for i in range(3):
            state, target = m.next_state(state, False)
            self.assertEqual(target, 'origin' if i == 2 else 'tunnel')

    def test_three_successes_required_to_recover(self):
        state = {'mode': 'origin'}
        for i in range(3):
            state, target = m.next_state(state, True)
            self.assertEqual(target, 'tunnel' if i == 2 else 'origin')

    def test_intermittent_probe_does_not_flap(self):
        state = {'mode': 'tunnel'}
        for healthy in [False, False, True, False, True]:
            state, target = m.next_state(state, healthy)
            self.assertEqual(target, 'tunnel')

    def setUp(self):
        self.config = dict(record_id='record', hostname='test.example',
                           tunnel_target='id.cfargotunnel.com', origin_ip='192.0.2.5')
        self.record = dict(id='record', name='test.example', proxied=True,
                           type='CNAME', content='id.cfargotunnel.com')

    def test_refuses_different_host_record_or_unproxied_record(self):
        for change in [dict(id='other'), dict(name='prod.example'), dict(proxied=False), dict(content='unrelated.example')]:
            with self.assertRaises(ValueError):
                m.validate_record({**self.record, **change}, self.config)

    def test_dns_write_stays_proxied_and_preserves_comment(self):
        original = {**self.record, 'comment': 'existing note'}
        result = {**self.record, 'type': 'A', 'content': '192.0.2.5'}
        with mock.patch.object(m, 'api', side_effect=[original, result]) as api:
            self.assertEqual(m.switch(self.config, 'disposable-test-token', 'origin'), 'origin')
        payload = api.call_args.args[2]
        self.assertTrue(payload['proxied'])
        self.assertEqual(payload['name'], 'test.example')
        self.assertEqual(payload['comment'], 'existing note')

    def test_health_requires_tunnel_ingress_and_ready_json(self):
        response = mock.MagicMock()
        response.status = 200
        response.headers = {'X-MusicBox-Ingress': 'tunnel'}
        response.read.return_value = b'{"protocol_version":2,"status":"ready"}'
        response.__enter__.return_value = response
        with mock.patch.object(m.urllib.request, 'urlopen', return_value=response):
            self.assertTrue(m.check_health({'health_url': 'https://health.example/health/ready'}))
            response.headers = {'X-MusicBox-Ingress': 'origin-fallback'}
            self.assertFalse(m.check_health({'health_url': 'https://health.example/health/ready'}))

    def test_non_ready_or_wrong_shape_health_is_rejected(self):
        headers = {'X-MusicBox-Ingress': 'tunnel'}
        self.assertFalse(m.ready_response(200, headers, b'[]'))
        self.assertFalse(m.ready_response(200, headers, b'{"status":"down"}'))
        self.assertFalse(m.ready_response(503, headers, b'{"status":"ready"}'))

    def test_unexpected_target_prevents_any_write(self):
        with mock.patch.object(m, 'api', return_value={**self.record, 'name': 'prod.example'}) as api:
            with self.assertRaises(ValueError):
                m.switch(self.config, 'disposable-test-token', 'origin')
        self.assertEqual(api.call_count, 1)

if __name__ == '__main__':
    unittest.main()
