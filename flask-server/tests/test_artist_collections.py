import copy
import unittest
from unittest.mock import Mock
from artist_collections import collection_cards, parse_artist_collections, get_artist_collections


def shelf(title, identity='VLPLone', endpoint=True):
    nav = {'browseEndpoint': {'browseId': identity}}
    return {'musicCarouselShelfRenderer': {
        'header': {'musicCarouselShelfBasicHeaderRenderer': {'title': {'runs': [{'text': title,
            'navigationEndpoint': {'browseEndpoint': {'browseId': 'UCartist', 'params': 'more'}}}]}}},
        'contents': [{'musicTwoRowItemRenderer': {'title': {'runs': [{'text': 'Collection',
            **({'navigationEndpoint': nav} if endpoint else {})}]},
            **({} if endpoint else {'navigationEndpoint': nav}),
            'thumbnailRenderer': {'musicThumbnailRenderer': {'thumbnail': {'thumbnails': [{'url': 'image'}]}}}}}]}}


class ArtistCollectionsTests(unittest.TestCase):
    def test_distinct_artist_playlists_and_featured_shelves_preserve_pagination(self):
        raw = {'contents': [shelf('Playlists by Singer'), shelf('Featured on', 'VLPLfeatured', False)]}
        before = copy.deepcopy(raw)
        sections = parse_artist_collections(raw)
        self.assertEqual(set(sections), {'playlists', 'featured'})
        self.assertEqual(sections['featured']['params'], 'more')
        cards = collection_cards(sections['featured'])
        self.assertEqual(cards[0]['playlistId'], 'VLPLfeatured')
        self.assertEqual(cards[0]['kind'], 'playlist')
        self.assertEqual(cards[0]['thumbnail'], 'image')
        self.assertEqual(raw, before)

    def test_missing_or_malformed_optional_sections_are_empty(self):
        self.assertEqual(parse_artist_collections({'contents': [shelf('Albums')]}), {})
        self.assertEqual(collection_cards({'results': [None, {}, {'playlistId': 'PLone'}, {'playlistId': 'PLone'}]}),
            [{'playlistId':'PLone', 'browseId':'PLone', 'title':'', 'kind':'playlist', 'thumbnail':''}])
        self.assertEqual(collection_cards(None), [])

    def test_optional_browse_is_cached_per_client_and_artist(self):
        client = Mock()
        client._send_request.return_value = {'contents': [shelf('Featured on')]}
        get_artist_collections(client, 'UCartist')
        get_artist_collections(client, 'UCartist')
        self.assertEqual(client._send_request.call_count, 1)
        other = Mock()
        other._send_request.return_value = {}
        self.assertEqual(get_artist_collections(other, 'UCartist'), {'additionalShelves': []})

    def test_failed_optional_browse_can_be_retried(self):
        client = Mock()
        client._send_request.side_effect = [OSError('offline'), {'contents': [shelf('Playlists')]}]
        with self.assertRaises(OSError):
            get_artist_collections(client, 'UCnew')
        self.assertIn('playlists', get_artist_collections(client, 'UCnew'))

class AdditionalArtistShelfTests(unittest.TestCase):
    def test_live_performances_and_library_items_keep_their_types(self):
        from artist_collections import parse_additional_artist_shelves
        video = {'musicTwoRowItemRenderer': {'title': {'runs': [{'text':'Live song'}]},
            'navigationEndpoint': {'watchEndpoint': {'videoId':'abcdefghijk'}}}}
        live = shelf('Live performances')
        live['musicCarouselShelfRenderer']['contents'] = [video, video]
        library = shelf('From your library', 'MPREalbum')
        result = parse_additional_artist_shelves({'contents':[shelf('Albums'), live, library]})
        self.assertEqual([s['title'] for s in result], ['Live performances','From your library'])
        self.assertEqual(len(result[0]['items']), 1)
        self.assertEqual(result[0]['items'][0]['video_id'], 'abcdefghijk')
        self.assertEqual(result[1]['items'][0]['kind'], 'album')
