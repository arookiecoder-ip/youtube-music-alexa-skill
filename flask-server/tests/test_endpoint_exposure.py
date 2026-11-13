"""Anonymous requests must not reach any registered private API handler."""
import sys
from pathlib import Path
from unittest import mock
sys.path[:0] = [str(Path(__file__).parent), str(Path(__file__).parents[1])]
from test_login_second_factor import SecondFactorDoorTestBase


class EndpointExposureTests(SecondFactorDoorTestBase):
    def setUp(self):
        super().setUp()
        self.bind_player_door()
        self.server._ensure_db()

    def test_every_registered_private_api_method_requires_credentials(self):
        app = self.server.app
        checked = 0
        for rule in app.url_map.iter_rules():
            path = rule.rule
            if (not self.server._is_api_path(path)
                    or self.server._is_spa_document_path(path)):
                continue
            # Account blueprint has independent auth, exercised by its own suite.
            if path.startswith('/api/account'):
                continue
            for argument in rule.arguments:
                path = __import__('re').sub(r'<(?:[^:>]+:)?' + argument + '>', 'audit-id', path)
            for method in rule.methods - {'OPTIONS', 'HEAD'}:
                with self.subTest(path=path, method=method):
                    handler = mock.Mock(return_value={'audit_handler_reached': True})
                    with mock.patch.dict(app.view_functions, {rule.endpoint: handler}):
                        response = self.client.open(path, method=method, json={} if method != 'GET' else None)
                    handler.assert_not_called()
                    self.assertIn(response.status_code, (401, 403, 404, 405))
                    checked += 1
        self.assertGreater(checked, 100)

    def test_proxy_gate_rejects_api_key_without_browser_session(self):
        response = self.client.get('/alexa/proxy_authorize/', headers={'X-Api-Key': self.server.API_KEY})
        self.assertEqual(response.status_code, 401)

    def test_proxy_gate_requires_the_accounts_active_attempt(self):
        from test_login_second_factor import PLAYER_PASSWORD
        user = self.player_user('proxy-audit')
        self.layer.alexa_access.set_enabled(user['id'], True)
        self.assertEqual(self.client.post('/login/', json={'username': user['username'], 'password': PLAYER_PASSWORD}).status_code, 200)
        with mock.patch.object(self.server.alexa_remote.remote, 'proxy_authorization', create=True, return_value=None):
            self.assertEqual(self.client.get('/alexa/proxy_authorize/').status_code, 403)
        with mock.patch.object(self.server.alexa_remote.remote, 'proxy_authorization', create=True, return_value='internal-test-credential'):
            response = self.client.get('/alexa/proxy_authorize/')
            self.assertEqual(response.status_code, 204)
            self.assertIn('no-store', response.headers['Cache-Control'])
