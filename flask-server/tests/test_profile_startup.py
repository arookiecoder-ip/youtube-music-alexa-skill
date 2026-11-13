"""Exercise the actual profile route without starting Alexa/browser services."""
import ast
import os
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
            '_alexa_access_enabled': lambda: True,
            'alexa_remote': SimpleNamespace(remote=SimpleNamespace(status=lambda: {'logged_in': True})),
            '_current_identity': lambda: 'owner',
            '_get_ytmusic_home': lambda identity=None: SimpleNamespace(auth_type='BROWSER'),
            '_ytmusic_client_is_authenticated': lambda yt: True,
            'Supporting': SimpleNamespace(get_ytdlp_cookies_file=lambda identity=None: '/server-cookies.txt'),
            'download_cookies': SimpleNamespace(status=self.probe, is_usable=lambda path: bool(path),
                                                ProbeBusy=type('ProbeBusy', (Exception,), {})),
            '_configure_cookie_probe': Mock(),
            'account_user_id': lambda identity: None,
            '_download_cookie_path': lambda identity: '/account-cookies.txt',
            '_server_download_cookie_path': lambda: '/server-cookies.txt',
            '_youtube_credentials': SimpleNamespace(path_for=lambda identity: ''),
            '_SERVER_IDENTITY': 'owner',
            '_server_session_enabled': lambda: True,
            'os': os,
            '_youtube_browser_sessions': SimpleNamespace(status=lambda identity=None: browser)}
        source = Path(__file__).resolve().parents[1] / 'server.py'
        route = next(node for node in ast.parse(source.read_text()).body if isinstance(node, ast.FunctionDef) and node.name == 'profile_status')
        exec(compile(ast.Module(body=[route], type_ignores=[]), str(source), 'exec'), namespace)
        self.namespace = namespace
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

    def test_disabled_amazon_has_no_status_debug_or_remote_lookup(self):
        status = Mock(side_effect=AssertionError('Disabled Amazon must not be contacted'))
        self.namespace['_alexa_access_enabled'] = lambda: False
        self.namespace['alexa_remote'].remote.status = status
        result = self.client.get('/api/profile_status/?audio_check=0').get_json()
        self.assertFalse(result['alexa_access_enabled'])
        self.assertNotIn('amazon_connected', result)
        self.assertNotIn('amazon', result['debug'])
        self.assertTrue(result['youtube_auth_working'])
        status.assert_not_called()
