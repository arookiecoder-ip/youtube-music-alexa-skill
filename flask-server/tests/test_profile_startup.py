"""Exercise the actual profile route without starting Alexa/browser services."""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
from flask import Flask, jsonify, request

class ProfileStartupTests(unittest.TestCase):
    def setUp(self):
        app = Flask(__name__)
        self.probe = Mock(return_value={'valid': True, 'message': 'Audio passed'})
        browser = {'available': True, 'refreshing': False, 'state': 'ready', 'reconnect_required': False, 'last_successful_renewal': 0}
        namespace = {'app': app, 'request': request, 'jsonify': jsonify,
            'alexa_remote': SimpleNamespace(remote=SimpleNamespace(status=lambda: {'logged_in': True})),
            '_get_ytmusic_home': lambda: SimpleNamespace(auth_type='BROWSER'),
            '_ytmusic_client_is_authenticated': lambda yt: True,
            'Supporting': SimpleNamespace(get_ytdlp_cookies_file=lambda: '/cookies.txt'),
            'download_cookies': SimpleNamespace(status=self.probe, ProbeBusy=type('ProbeBusy', (Exception,), {})),
            '_configure_cookie_probe': Mock(), '_youtube_browser_sessions': SimpleNamespace(status=lambda: browser)}
        source = Path(__file__).resolve().parents[1] / 'server.py'
        route = next(node for node in ast.parse(source.read_text()).body if isinstance(node, ast.FunctionDef) and node.name == 'profile_status')
        exec(compile(ast.Module(body=[route], type_ignores=[]), str(source), 'exec'), namespace)
        self.client = app.test_client()

    def test_native_startup_checks_accounts_without_audio_download(self):
        result = self.client.get('/api/profile_status/?audio_check=0').get_json()
        self.assertTrue(result['amazon_connected'])
        self.assertTrue(result['youtube_auth_working'])
        self.assertTrue(result['youtube_cookies_present'])
        self.assertFalse(result['youtube_cookies_working'])
        self.probe.assert_not_called()

    def test_existing_profile_checks_still_validate_download_access(self):
        result = self.client.get('/api/profile_status/').get_json()
        self.assertTrue(result['youtube_cookies_working'])
        self.probe.assert_called_once()

if __name__ == '__main__':
    unittest.main()
