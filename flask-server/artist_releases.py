"""Normalize YouTube release shelves before the upstream album parser sees them."""
from copy import deepcopy
from ytmusicapi import YTMusic


def normalize_release_response(response):
    response = deepcopy(response)

    def find_shelf(node):
        if isinstance(node, dict):
            if 'gridRenderer' in node:
                return node['gridRenderer']
            if 'musicCarouselShelfRenderer' in node or 'musicShelfRenderer' in node:
                shelf = node.get('musicCarouselShelfRenderer') or node['musicShelfRenderer']
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

    def normalize_items(items):
        normalized = []
        for item in items:
            row = item.get('musicResponsiveListItemRenderer')
            if row:
                columns = row.get('flexColumns') or []
                def column(index):
                    return (columns[index].get('musicResponsiveListItemFlexColumnRenderer') or {}).get('text') or {} if index < len(columns) else {}
                title = column(0)
                runs = title.get('runs') or []
                if runs and not runs[0].get('navigationEndpoint') and row.get('navigationEndpoint'):
                    runs[0]['navigationEndpoint'] = row['navigationEndpoint']
                item = {'musicTwoRowItemRenderer': {'title': title, 'subtitle': column(1),
                    'thumbnailRenderer': row.get('thumbnail') or {}, 'menu': row.get('menu') or {}}}
            card = item.get('musicTwoRowItemRenderer')
            if card:
                # Empty subtitle runs are legal; the upstream parser indexes run 0.
                subtitle = card.get('subtitle') or {}
                if not subtitle.get('runs'):
                    card['subtitle'] = {}
                normalized.append(item)
        return normalized

    shelf = find_shelf(response.get('contents', {}))
    if shelf is not None:
        shelf['items'] = normalize_items(shelf.get('items') or [])
        # get_artist_albums assumes a single-column grid, even when its own
        # contents lookup successfully found a carousel. Preserve pagination.
        response['contents'] = {'singleColumnBrowseResultsRenderer': {'tabs': [
            {'tabRenderer': {'content': {'sectionListRenderer': {'contents': [
                {'gridRenderer': shelf}]}}}}]}}
    continuations = response.get('continuationContents', {})
    for kind in ('musicCarouselShelfContinuation', 'musicShelfContinuation'):
        if kind not in continuations:
            continue
        shelf = continuations.pop(kind)
        continuations['gridContinuation'] = {**shelf, 'items': shelf.get('contents', [])}
    if 'gridContinuation' in continuations:
        grid = continuations['gridContinuation']
        grid['items'] = normalize_items(grid.get('items') or [])
    return response


def get_artist_releases(client, browse_id, params, limit):
    # Never patch the shared authenticated client's request function.
    initial = normalize_release_response(client._send_request('browse', {'browseId': browse_id, 'params': params}))
    contents = initial.get('contents', {}).get('singleColumnBrowseResultsRenderer', {}).get('tabs', [])
    if contents:
        grid = contents[0].get('tabRenderer', {}).get('content', {}).get('sectionListRenderer', {}).get('contents', [])
        if grid and not grid[0].get('gridRenderer', {}).get('items') and not grid[0].get('gridRenderer', {}).get('continuations'):
            return []
    parser = YTMusic()
    first = True
    def send(*args, **kwargs):
        nonlocal first
        if first:
            first = False
            return initial
        return normalize_release_response(client._send_request(*args, **kwargs))
    parser._send_request = send
    return parser.get_artist_albums(browse_id, params, limit)
