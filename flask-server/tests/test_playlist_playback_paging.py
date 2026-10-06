import ast
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from playlist_paging import playlist_page_request

class PlaylistPlaybackPagingTests(unittest.TestCase):
    def test_thousand_song_queue_uses_one_continuation_traversal(self):
        limit, offset = playlist_page_request({'playback': '1', 'limit': '5000'})
        tracks = list(range(1000))
        self.assertEqual(tracks[offset:offset + limit], tracks)
        self.assertEqual(offset, 0)
    def test_bulk_is_bounded_and_normal_browser_pages_stay_small(self):
        self.assertEqual(playlist_page_request({'playback': '1', 'limit': '90000'}), (5000, 0))
        self.assertEqual(playlist_page_request({'limit': '90000', 'offset': '100'}), (100, 100))
        self.assertEqual(playlist_page_request({}), (30, 0))
    def test_invalid_and_partial_bulk_parameters_fail(self):
        for args in ({'limit': 'nan'}, {'offset': 'nan'}, {'playback': '1', 'offset': '1'}):
            with self.assertRaises(ValueError): playlist_page_request(args)
    def test_phone_control_traffic_does_not_use_browse_or_audio_budget(self):
        source = Path(__file__).resolve().parents[1] / 'server.py'
        node = next(n for n in ast.parse(source.read_text()).body if isinstance(n, ast.FunctionDef) and n.name == '_rate_limit_group')
        scope = {}; exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), 'exec'), scope)
        group = scope['_rate_limit_group']
        self.assertEqual(group('/api/app/output'), 'shared_playback')
        self.assertEqual(group('/api/app/queue'), 'shared_playback')
        self.assertEqual(group('/api/library/playlists/PL123'), 'api')
        self.assertEqual(group('/audio/song'), 'audio')
        self.assertEqual(group('/proxy'), 'proxy')

    def test_actual_bulk_route_fetches_each_prefix_once_and_returns_all_thousand_songs(self):
        import asyncio
        from flask import Flask, jsonify, request
        from types import SimpleNamespace
        from unittest.mock import Mock
        source = Path(__file__).resolve().parents[1] / 'server.py'
        node = next(n for n in ast.parse(source.read_text()).body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'api_get_library_playlist')
        node.decorator_list = []
        tracks = [{'videoId': str(i)} for i in range(1000)]
        provider = SimpleNamespace(get_playlist=Mock(return_value={'title': 'Large mix', 'tracks': tracks, 'trackCount': 1000}))
        scope = dict(asyncio=asyncio, jsonify=jsonify, request=request,
            _detail_id_has_known_shape=lambda *args: True, _jam_guest=lambda: False, _get_ytmusic_home=lambda: provider)
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), 'exec'), scope)
        app = Flask(__name__)
        with app.test_request_context('/api/library/playlists/PL123?playback=1&limit=5000'):
            result = asyncio.run(scope['api_get_library_playlist']('PL123'))
        provider.get_playlist.assert_called_once_with('PL123', 5001)
        self.assertEqual(result.json['tracks'], tracks)
        self.assertFalse(result.json['has_more'])

    def test_cached_audio_bursts_do_not_consume_cold_download_budget(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        source = Path(__file__).resolve().parents[1] / 'server.py'
        node = next(n for n in ast.parse(source.read_text()).body if isinstance(n, ast.FunctionDef) and n.name == '_audio_delivery_rate_group')
        cached = Mock(side_effect=lambda video: '/audio/song.m4a' if video == 'aaaaaaaaaaa' else None)
        scope = {'_valid_video_id': lambda video: isinstance(video, str) and len(video) == 11,
                 'Supporting': SimpleNamespace(cached_audio_path=cached)}
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), 'exec'), scope)
        group = scope['_audio_delivery_rate_group']
        self.assertEqual(group('aaaaaaaaaaa'), 'cached_audio')
        self.assertEqual(group('bbbbbbbbbbb'), 'audio')
        self.assertEqual(group(None), 'audio')
        self.assertEqual(group('../path'), 'audio')
        self.assertEqual(cached.call_count, 2)
