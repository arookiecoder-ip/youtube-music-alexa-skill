"""Exercise the real search route without YouTube/account side effects."""
import ast
import collections
import logging
from pathlib import Path
import re
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from flask import Flask, jsonify, request

class SearchAudioFallbackTests(unittest.TestCase):
    def run_search(self, mixed, audio=None, failure=False):
        app = Flask(__name__)
        client = Mock()
        def search(**kwargs):
            if kwargs.get('filter') == 'songs':
                if failure: raise TimeoutError('slow upstream')
                return audio or []
            return mixed
        client.search.side_effect = search
        tree = ast.parse((Path(__file__).parents[1] / 'server.py').read_text())
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'alexa_search')
        env = dict(Supporting=SimpleNamespace(duration_ms=lambda item: 180000), app=app, request=request, jsonify=jsonify, collections=collections, re=re,
                   _artist_entries_from_item=lambda item, fallback="": item.get("artists", []),
                   YTMusic=lambda: client, logger=logging.getLogger('search-test'),
                   _valid_video_id=lambda value: bool(re.fullmatch(r'[a-zA-Z0-9_-]{11}', value or '')),
                   error_response=lambda message, status: (jsonify(error=message), status))
        exec(compile(ast.Module(body=[function], type_ignores=[]), 'server-search-route', 'exec'), env)
        return app.test_client().get('/alexa/search/?q=Song').get_json(), client
    def song(self, kind='song'):
        return dict(resultType=kind, videoId='abcdefghijk', title='Song', artists=[dict(name='Artist')])
    def test_artist_album_only_response_still_returns_songs(self):
        result, client = self.run_search([dict(resultType='album', browseId='MPRE1', title='Album')], [self.song()])
        self.assertEqual(result['songs'][0]['video_id'], 'abcdefghijk')
        self.assertEqual(result['albums'][0]['title'], 'Album')
        self.assertEqual(client.search.call_args.kwargs['filter'], 'songs')
    def test_video_only_response_prefers_audio_and_deduplicates(self):
        result, _ = self.run_search([self.song('video')], [self.song()])
        self.assertEqual(len(result['songs']), 1)
        self.assertEqual(result['songs'][0]['resultType'], 'song')
    def test_existing_audio_does_not_trigger_an_extra_request(self):
        result, client = self.run_search([self.song()])
        self.assertEqual(len(result['songs']), 1)
        self.assertEqual(client.search.call_count, 1)
    def test_optional_audio_timeout_keeps_successful_mixed_results(self):
        result, _ = self.run_search([dict(resultType='album', browseId='MPRE1', title='Album')], failure=True)
        self.assertEqual(result['albums'][0]['title'], 'Album')

if __name__ == '__main__': unittest.main()
