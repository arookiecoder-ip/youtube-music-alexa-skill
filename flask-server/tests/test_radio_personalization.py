import ast
import asyncio
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock

from radio_auth import cookie_headers


class RadioPersonalizationTests(unittest.TestCase):
    def setUp(self):
        tree = ast.parse((Path(__file__).resolve().parents[1] / 'server.py').read_text())
        self.ns = dict(_current_identity=lambda: 'user:alice', _yt_auth_file=Mock(return_value='alice.json'),
                       _get_ytmusic_home=Mock(), _server_session_enabled=lambda: True,
                       _has_server_session=lambda: True, _SERVER_IDENTITY='server',
                       Supporting=Mock(), YTMusic=Mock(), _get_ytmusic=Mock(), logger=Mock())
        node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_get_ytmusic_radio')
        exec(compile(ast.Module(body=[node], type_ignores=[]), 'server.py', 'exec'), self.ns)
        self.select = self.ns['_get_ytmusic_radio']

    def test_linked_listener_uses_only_own_session(self):
        self.select()
        self.ns['_get_ytmusic_home'].assert_called_once_with('user:alice')
        self.ns['_get_ytmusic'].assert_not_called()

    def test_unlinked_listener_uses_server_session_for_radio(self):
        self.ns['_yt_auth_file'].return_value = None
        self.select()
        self.ns['_get_ytmusic_home'].assert_called_once_with('server')

    def test_download_export_fallback_does_not_change_account_client(self):
        self.ns['_yt_auth_file'].return_value = None
        self.ns['_has_server_session'] = lambda: False
        with tempfile.NamedTemporaryFile(mode='w') as stream:
            expiry = int(time.time()) + 3600
            stream.write(f'# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t{expiry}\tSAPISID\tfixture\n')
            stream.flush()
            self.ns['Supporting'].get_ytdlp_cookies_file.return_value = stream.name
            self.select()
        self.ns['_get_ytmusic_home'].assert_not_called()
        headers = self.ns['YTMusic'].call_args.kwargs['auth']
        self.assertIn('SAPISID=fixture', headers['cookie'])
        self.assertTrue(headers['authorization'].startswith('SAPISIDHASH '))

    def test_each_radio_request_uses_its_own_seed_and_provider_order(self):
        tree = ast.parse((Path(__file__).resolve().parents[1] / 'server.py').read_text())
        supporting = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'Supporting')
        node = next(n for n in supporting.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'get_radio_queue')
        node.decorator_list = []
        client = Mock()
        def playlist(**kwargs):
            seed = kwargs['videoId']
            return {'tracks': [{'videoId': seed, 'title': seed}, {'videoId': seed},
                               {'videoId': 'invalid'}, None, {'videoId': 'next' + seed[4:], 'title': 'Related', 'thumbnail': {'url': 'https://example.test/art.jpg'}}]}
        client.get_watch_playlist.side_effect = playlist
        ns = dict(asyncio=asyncio, _get_ytmusic_radio=lambda: client, logger=Mock(),
                  _valid_video_id=lambda value: isinstance(value, str) and len(value) == 11,
                  _artist_credit_from_list=lambda value: '', _artist_entries_from_item=lambda value: [],
                  Supporting=Mock())
        ns['Supporting'].duration_ms.return_value = 0
        exec(compile(ast.Module(body=[node], type_ignores=[]), 'server.py', 'exec'), ns)
        first = asyncio.run(ns['get_radio_queue']('aaaaaaaaaaa'))
        second = asyncio.run(ns['get_radio_queue']('bbbbbbbbbbb'))
        self.assertEqual([row['video_id'] for row in first], ['aaaaaaaaaaa', 'nextaaaaaaa'])
        self.assertEqual([row['video_id'] for row in second], ['bbbbbbbbbbb', 'nextbbbbbbb'])
        self.assertEqual(client.get_watch_playlist.call_args_list[0].kwargs,
                         dict(videoId='aaaaaaaaaaa', radio=True, limit=50))

    def test_unusable_export_cannot_supply_unrelated_or_expired_cookies(self):
        with tempfile.NamedTemporaryFile(mode='w') as stream:
            stream.write('# Netscape HTTP Cookie File\n.other.example\tTRUE\t/\tTRUE\t9999999999\tSAPISID\twrong\n.youtube.com\tTRUE\t/\tTRUE\t1\tSAPISID\texpired\n')
            stream.flush()
            with self.assertRaises(ValueError):
                cookie_headers(stream.name)


if __name__ == '__main__':
    unittest.main()
