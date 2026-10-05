"""Run the production auth middleware without contacting Alexa or modifying a live queue."""
import ast
from pathlib import Path
import hmac
import unittest
from flask import Flask, jsonify, request, session


class QueueSessionAuthTests(unittest.TestCase):
    def setUp(self):
        source = Path(__file__).resolve().parents[1] / 'server.py'
        names = {'_SESSION_PATHS', '_SESSION_PREFIXES', '_API_PATH_ROOTS',
                 '_is_web_session_path', '_is_api_path', 'require_api_key'}
        nodes = []
        for node in ast.parse(source.read_text()).body:
            if isinstance(node, ast.FunctionDef) and node.name in names:
                nodes.append(node)
            elif isinstance(node, ast.Assign) and any(
                    isinstance(target, ast.Name) and target.id in names for target in node.targets):
                nodes.append(node)
        self.app = Flask(__name__)
        self.app.secret_key = 'test-only'
        scope = {'app': self.app, 'request': request, 'session': session,
                 'jsonify': jsonify, 'hmac': hmac, 'API_KEY': 'test-machine-key',
                 '_ensure_db': lambda: None, '_is_spa_document_path': lambda path: False,
                 '_PUBLIC_PATHS': (), '_PUBLIC_PREFIXES': (),
                 '_logged_in': lambda: bool(session.get('owner')), '_jam_guest': lambda: False}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), 'exec'), scope)
        self.is_session_path = scope['_is_web_session_path']
        # A no-op handler proves the middleware passed without queue side effects.
        self.app.add_url_rule('/api/app/queue/', 'queue_probe',
                              lambda: jsonify(ok=True), methods=['POST'], strict_slashes=False)
        self.client = self.app.test_client()

    def test_owner_json_queue_post_accepts_both_path_forms(self):
        with self.client.session_transaction() as saved:
            saved['owner'] = True
        for path in ['/api/app/queue', '/api/app/queue/']:
            response = self.client.post(path, json={})
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.json['ok'])

    def test_missing_session_is_classified_consistently(self):
        for path in ['/api/app/queue', '/api/app/queue/']:
            response = self.client.post(path, json={})
            self.assertEqual(response.status_code, 401)
            self.assertEqual(response.json['error']['code'], 'web_session_required')

    def test_owner_form_post_still_rejects_csrf(self):
        with self.client.session_transaction() as saved:
            saved['owner'] = True
        for path in ['/api/app/queue', '/api/app/queue/']:
            self.assertEqual(self.client.post(path, data={'action': 'current'}).status_code, 401)

    def test_invalid_machine_key_does_not_gain_access(self):
        response = self.client.post('/api/app/queue/', json={}, headers={'X-Api-Key': 'wrong'})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json['error']['code'], 'unauthorized')

    def test_lookalike_paths_are_not_authorized_as_queue(self):
        self.assertFalse(self.is_session_path('/api/app/queue-unsafe'))
        self.assertFalse(self.is_session_path('/api/app/queues'))


if __name__ == '__main__':
    unittest.main()
