"""Optional artist playlist shelves absent from the upstream artist parser."""
import threading
import time

_cache = {}
_lock = threading.Lock()


def collection_cards(section):
    results = (section or {}).get('results') or []
    out, seen = [], set()
    for row in results:
        if not isinstance(row, dict):
            continue
        identity = row.get('playlistId') or row.get('browseId') or ''
        if not identity or identity in seen:
            continue
        seen.add(identity)
        thumbs = row.get('thumbnails') or []
        out.append({**row, 'browseId': identity, 'playlistId': identity,
                    'kind': 'playlist', 'title': row.get('title') or '',
                    'thumbnail': row.get('thumbnail') or (thumbs[-1].get('url', '') if thumbs else '')})
    return out


def parse_artist_collections(response):
    sections = {}
    def visit(node):
        if isinstance(node, list):
            for child in node:
                visit(child)
        elif isinstance(node, dict):
            shelf = node.get('musicCarouselShelfRenderer')
            if shelf:
                header = (shelf.get('header') or {}).get('musicCarouselShelfBasicHeaderRenderer') or {}
                title = ''.join(r.get('text', '') for r in (header.get('title') or {}).get('runs', [])).strip().lower()
                kind = 'featured' if title == 'featured on' else 'playlists' if title == 'playlists' or title.startswith('playlists by ') else ''
                if kind:
                    endpoint = ((header.get('title') or {}).get('runs') or [{}])[0].get('navigationEndpoint') or {}
                    endpoint = endpoint.get('browseEndpoint') or {}
                    rows = []
                    for item in shelf.get('contents') or []:
                        card = item.get('musicTwoRowItemRenderer') or {}
                        runs = (card.get('title') or {}).get('runs') or []
                        nav = card.get('navigationEndpoint') or (runs[0].get('navigationEndpoint') if runs else {}) or {}
                        bid = (nav.get('browseEndpoint') or {}).get('browseId') or ''
                        playlist = ((card.get('overlay') or {}).get('musicItemThumbnailOverlayRenderer') or {}).get('content') or {}
                        playlist = (((playlist.get('musicPlayButtonRenderer') or {}).get('playNavigationEndpoint') or {}).get('watchPlaylistEndpoint') or {}).get('playlistId') or ''
                        if bid or playlist:
                            rows.append({'browseId': bid or playlist, 'playlistId': playlist or bid,
                                'title': ''.join(r.get('text', '') for r in runs),
                                'thumbnails': (((card.get('thumbnailRenderer') or {}).get('musicThumbnailRenderer') or {}).get('thumbnail') or {}).get('thumbnails') or []})
                    sections[kind] = {'browseId': endpoint.get('browseId'), 'params': endpoint.get('params'), 'results': rows}
            for child in node.values():
                visit(child)
    visit(response)
    return sections


def get_artist_collections(client, channel_id):
    # Avoid doubling browse traffic when an artist is revisited or paginated.
    # Cache by client identity so authenticated/guest requests never share data.
    key = (client, channel_id)
    with _lock:
        cached = _cache.get(key)
        if cached and cached[0] > time.monotonic():
            return cached[1]
    response = client._send_request('browse', {'browseId': channel_id})
    sections = parse_artist_collections(response)
    sections['additionalShelves'] = parse_additional_artist_shelves(response)
    with _lock:
        if len(_cache) >= 128:
            _cache.pop(next(iter(_cache)))
        _cache[key] = (time.monotonic() + 300, sections)
    return sections


def artist_shelf_item(item):
    """Normalize track, album, artist and playlist cards without title heuristics."""
    card = item.get('musicTwoRowItemRenderer') or item.get('musicResponsiveListItemRenderer') or {}
    columns = card.get('flexColumns') or []
    def column(index):
        return (columns[index].get('musicResponsiveListItemFlexColumnRenderer') or {}).get('text') or {} if index < len(columns) else {}
    title = card.get('title') or column(0)
    runs = title.get('runs') or []
    nav = card.get('navigationEndpoint') or (runs[0].get('navigationEndpoint') if runs else {}) or {}
    browse_id = (nav.get('browseEndpoint') or {}).get('browseId') or ''
    video_id = (nav.get('watchEndpoint') or {}).get('videoId') or (card.get('playlistItemData') or {}).get('videoId') or ''
    if not browse_id and not video_id:
        return None
    subtitle = card.get('subtitle') or column(1)
    artists = []
    for run in subtitle.get('runs') or []:
        artist_id = ((run.get('navigationEndpoint') or {}).get('browseEndpoint') or {}).get('browseId') or ''
        if artist_id.startswith('UC'):
            artists.append({'name':run.get('text') or '', 'id':artist_id})
    thumbnail = card.get('thumbnailRenderer') or card.get('thumbnail') or {}
    thumbs = ((thumbnail.get('musicThumbnailRenderer') or {}).get('thumbnail') or {}).get('thumbnails') or []
    kind = 'track' if video_id else 'artist' if browse_id.startswith('UC') else 'album' if browse_id.startswith('MPRE') else 'playlist'
    return {'kind':kind, 'browseId':browse_id, 'playlistId':browse_id if kind=='playlist' else '',
            'video_id':video_id, 'videoId':video_id, 'title':''.join(r.get('text','') for r in runs),
            'artists':artists, 'artist':', '.join(a['name'] for a in artists),
            'thumbnail':thumbs[-1].get('url','') if thumbs else ''}


def parse_additional_artist_shelves(response):
    import hashlib
    shelves = []
    handled = {'top songs','songs','albums','singles & eps','singles','videos','music videos',
               'featured on','playlists','fans might also like','related artists','related','about'}
    def walk(node):
        if isinstance(node,list):
            for child in node:
                walk(child)
        elif isinstance(node,dict):
            shelf = node.get('musicCarouselShelfRenderer') or node.get('musicShelfRenderer')
            if shelf:
                header = (shelf.get('header') or {}).get('musicCarouselShelfBasicHeaderRenderer') or shelf
                title = ''.join(r.get('text','') for r in (header.get('title') or {}).get('runs',[])).strip()
                if title and title.lower() not in handled and not title.lower().startswith('playlists by '):
                    items, seen = [], set()
                    for raw in shelf.get('contents') or []:
                        item = artist_shelf_item(raw)
                        key = (item.get('kind'),item.get('video_id') or item.get('browseId')) if item else None
                        if item and key not in seen:
                            items.append(item); seen.add(key)
                    if items:
                        shelves.append({'id':hashlib.sha256(title.encode()).hexdigest()[:16], 'title':title, 'items':items})
            for child in node.values():
                walk(child)
    walk(response)
    return shelves
