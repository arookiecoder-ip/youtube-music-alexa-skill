"""Normalize YouTube release shelves before the upstream album parser sees them."""
from copy import deepcopy
from ytmusicapi import YTMusic


def normalize_release_response(response):
    response = deepcopy(response)

    def find_shelf(node):
        if isinstance(node, dict):
            if 'gridRenderer' in node:
                return node['gridRenderer']
            if 'musicCarouselShelfRenderer' in node:
                shelf = node['musicCarouselShelfRenderer']
                return {**shelf, 'items': shelf.get('contents', [])}
            for child in node.values():
                found = find_shelf(child)
                if found is not None:
                    return found
        elif isinstance(node, list):
            for child in node:
                found = find_shelf(child)
                if found is not None:
                    return found
        return None

    shelf = find_shelf(response.get('contents', {}))
    if shelf is not None:
        # get_artist_albums assumes a single-column grid, even when its own
        # contents lookup successfully found a carousel. Preserve pagination.
        response['contents'] = {'singleColumnBrowseResultsRenderer': {'tabs': [
            {'tabRenderer': {'content': {'sectionListRenderer': {'contents': [
                {'gridRenderer': shelf}]}}}}]}}
    continuations = response.get('continuationContents', {})
    if 'musicCarouselShelfContinuation' in continuations:
        shelf = continuations.pop('musicCarouselShelfContinuation')
        continuations['gridContinuation'] = {**shelf, 'items': shelf.get('contents', [])}
    return response


def get_artist_releases(client, browse_id, params, limit):
    # Never patch the shared authenticated client's request function.
    parser = YTMusic()
    parser._send_request = lambda *args, **kwargs: normalize_release_response(
        client._send_request(*args, **kwargs))
    return parser.get_artist_albums(browse_id, params, limit)
