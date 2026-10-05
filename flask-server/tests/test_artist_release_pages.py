import ast
import asyncio
import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, AsyncMock

from flask import Flask, jsonify, request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from artist_releases import get_artist_releases, normalize_release_response


def release(title='Album', browse_id='MPREalbum'):
    return {'musicTwoRowItemRenderer': {
        'title': {'runs': [{'text': title, 'navigationEndpoint': {'browseEndpoint': {'browseId': browse_id}}}]},
        'subtitle': {'runs': [{'text': 'Album'}]},
        'thumbnailRenderer': {'musicThumbnailRenderer': {'thumbnail': {'thumbnails': [{'url': 'https://image'}]}}}}}


def response(shelf, two_column=False):
    return {'contents': {('twoColumnBrowseResultsRenderer' if two_column else 'singleColumnBrowseResultsRenderer'): {
        'tabs': [{'tabRenderer': {'content': {'sectionListRenderer': {'contents': [shelf]}}}}]}}}


class ReleaseRendererTests(unittest.TestCase):
    def test_carousel_is_parsed_without_none_grid_crash(self):
        raw = response({'musicCarouselShelfRenderer': {'contents': [release()]}})
        original = copy.deepcopy(raw)
        client = Mock()
        client._send_request.return_value = raw
        result = get_artist_releases(client, 'UCartist', 'params', 31)
        self.assertEqual(result[0]['browseId'], 'MPREalbum')
        self.assertEqual(raw, original)

    def test_two_column_grid_uses_same_parser(self):
        client = Mock()
        client._send_request.return_value = response({'gridRenderer': {'items': [release()]}}, True)
        self.assertEqual(get_artist_releases(client, 'UCartist', 'params', 31)[0]['title'], 'Album')

    def test_carousel_continuation_preserves_paging_token(self):
        raw = {'continuationContents': {'musicCarouselShelfContinuation': {
            'contents': [release()], 'continuations': [{'nextContinuationData': {'continuation': 'next'}}]}}}
        normalized = normalize_release_response(raw)['continuationContents']['gridContinuation']
        self.assertEqual(normalized['items'], [release()])
        self.assertEqual(normalized['continuations'][0]['nextContinuationData']['continuation'], 'next')

    def test_following_carousel_continuation_loads_remaining_releases(self):
        first = response({'musicCarouselShelfRenderer': {
            'contents': [release('First', 'MPREone')],
            'continuations': [{'nextContinuationData': {'continuation': 'next'}}]}})
        following = {'continuationContents': {'musicCarouselShelfContinuation': {
            'contents': [release('Second', 'MPREtwo')]}}}
        client = Mock()
        client._send_request.side_effect = [first, following]
        result = get_artist_releases(client, 'UCartist', 'params', 31)
        self.assertEqual([item['browseId'] for item in result], ['MPREone', 'MPREtwo'])
        self.assertEqual(client._send_request.call_count, 2)

    def test_playlist_browse_identity_is_preserved(self):
        client = Mock()
        client._send_request.return_value = response({'gridRenderer': {'items': [release('Playlist', 'VLPLplaylist')]}})
        self.assertEqual(get_artist_releases(client, 'UCartist', 'params', 31)[0]['browseId'], 'VLPLplaylist')


class ReleaseEndpointTests(unittest.TestCase):
    def setUp(self):
        # Exercise the actual route body without starting unrelated Alexa services.
        source = Path(__file__).resolve().parents[1] / 'server.py'
        tree = ast.parse(source.read_text())
        route = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == 'api_artist_releases')
        route.decorator_list = []
        self.artist = AsyncMock()
        self.client = Mock()
        self.namespace = {'asyncio': asyncio, 'jsonify': jsonify, 'request': request,
            '_detail_id_has_known_shape': lambda *a: True, '_jam_guest': lambda: False,
            '_get_ytmusic_home': lambda: self.client, 'api_get_artist': self.artist, 'logger': Mock()}
        exec(compile(ast.Module(body=[route], type_ignores=[]), str(source), 'exec'), self.namespace)
        self.app = Flask(__name__)

    def test_uses_resilient_artist_page_and_slices_bounded_page(self):
        with self.app.test_request_context('/?kind=albums&offset=1&limit=1'):
            self.artist.return_value = jsonify({'artist': {'albums': {'results': [
                {'browseId': 'one'}, {'browseId': 'two'}, {'browseId': 'three'}]}}})
            result = asyncio.run(self.namespace['api_artist_releases']('UCartist')).get_json()
        self.artist.assert_awaited_once_with('UCartist')
        self.assertEqual(result, {'items': [{'browseId': 'two'}], 'next_offset': 2, 'has_more': True})
        self.client.get_artist.assert_not_called()

    def test_artist_authentication_failure_is_preserved(self):
        with self.app.test_request_context('/?kind=albums'):
            self.artist.return_value = (jsonify({'error': 'Sign in again'}), 401)
            result, status = asyncio.run(self.namespace['api_artist_releases']('UCartist'))
        self.assertEqual(status, 401)
        self.assertEqual(result.get_json()['error'], 'Sign in again')

    def test_invalid_category_does_not_call_provider(self):
        with self.app.test_request_context('/?kind=invalid'):
            _, status = asyncio.run(self.namespace['api_artist_releases']('UCartist'))
        self.assertEqual(status, 400)
        self.artist.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
