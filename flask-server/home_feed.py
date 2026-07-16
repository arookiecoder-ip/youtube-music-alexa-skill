import time
import copy
import hashlib

SCHEMA_VERSION = 2

# Allowed ENUMS
LAYOUTS = {'shortcuts', 'song_grid', 'cards', 'circles', 'wide_cards'}
KINDS = {'track', 'playlist', 'album', 'artist', 'station'}

# Timeout constraints for server calls
SOURCE_TIMEOUTS = {
    'local': 5.0,
    'ytmusic_home': 10.0,
    'ytmusic_library': 10.0,
    'ytmusic_explore': 10.0
}

# Shelf/Item constraints
MAX_SHELVES = 12
MIN_ITEMS_PER_SHELF = 2
MAX_ITEMS_PER_SHELF = 24
MAX_MOUNTED_ITEMS = 120

def _get_text(runs):
    """Safely extract text from a ytmusicapi 'runs' list or string."""
    if not runs:
        return ""
    if isinstance(runs, str):
        return runs
    if isinstance(runs, list):
        return "".join(run.get("text", "") for run in runs if isinstance(run, dict))
    return ""

def _get_best_thumbnail(thumbnails):
    """Safely select the highest quality thumbnail."""
    if not thumbnails or not isinstance(thumbnails, list):
        return ""
    # Find thumbnail with max width
    best = max(thumbnails, key=lambda t: t.get('width', 0) if isinstance(t, dict) else 0, default=None)
    return best.get('url', "") if best and isinstance(best, dict) else ""

def _construct_target(kind, item_id):
    if not item_id:
        return None
    return {"kind": kind, "id": item_id}

def _construct_play(kind, video_id=None, playlist_id=None):
    if video_id:
        return {"videoId": video_id}
    if playlist_id:
        return {"playlistId": playlist_id}
    return None

def _canonical_playlist_id(playlist_id):
    """Turn Music browse playlist ids (``VLPL...``/``VLRD...``) into
    the ids accepted by ytmusicapi (``PL...``/``RD...``)."""
    playlist_id = str(playlist_id or "").strip()
    if playlist_id.startswith("VL") and playlist_id[2:].startswith(("PL", "RD", "OLAK", "LM")):
        return playlist_id[2:]
    return playlist_id

def normalize_track(item):
    if not item or not isinstance(item, dict):
        return None
    video_id = item.get("videoId") or item.get("video_id")
    if not video_id:
        return None

    title = _get_text(item.get("title"))
    raw_artists = item.get("artists") or item.get("artist") or []
    if isinstance(raw_artists, dict):
        raw_artists = [raw_artists]
    artists = [
        {
            "name": _get_text(a.get("name")),
            "id": a.get("id") or a.get("browseId") or a.get("channelId") or ""
        }
        for a in raw_artists if isinstance(a, dict) and a.get("name")
    ]
    artists_text = ", ".join(a["name"] for a in artists)
    album = item.get("album") or {}
    if isinstance(album, str):
        album = {"name": album}
    album_text = _get_text(album.get("name")) if album else ""
    album_id = (album.get("id") or album.get("browseId") or
                item.get("albumId") or item.get("album_id") or
                item.get("albumBrowseId") or "")
    artist_id = artists[0]["id"] if artists else ""
    
    subtitle_parts = [p for p in (artists_text, album_text) if p]
    subtitle = " \u2022 ".join(subtitle_parts)

    return {
        "kind": "track",
        "key": f"track_{video_id}",
        "title": title,
        "subtitle": subtitle,
        "artists": artists,
        "album": album_text,
        "albumId": album_id,
        "artistId": artist_id,
        "channelId": artist_id,
        "image": _get_best_thumbnail(item.get("thumbnails")),
        "images": item.get("thumbnails", []),
        "videoId": video_id,
        "target": _construct_target("track", video_id),
        "play": _construct_play("track", video_id=video_id),
        "capabilities": {
            "play": True,
            "like": True,
            "queue": True,
            "playlist": True
        }
    }

def normalize_album(item):
    if not item or not isinstance(item, dict):
        return None
    browse_id = item.get("browseId")
    if not browse_id:
        return None

    title = _get_text(item.get("title"))
    artists = [
        {
            "name": _get_text(a.get("name")),
            "id": a.get("id") or a.get("browseId") or ""
        }
        for a in item.get("artists", []) if a.get("name")
    ]
    subtitle = ", ".join(a["name"] for a in artists)

    return {
        "kind": "album",
        "key": f"album_{browse_id}",
        "title": title,
        "subtitle": subtitle,
        "artists": artists,
        "image": _get_best_thumbnail(item.get("thumbnails")),
        "images": item.get("thumbnails", []),
        "browseId": browse_id,
        "target": _construct_target("album", browse_id),
        "play": _construct_play("album", playlist_id=item.get("audioPlaylistId") or item.get("playlistId")),
        "capabilities": {
            "play": bool(item.get("audioPlaylistId") or item.get("playlistId")),
            "like": False,
            "queue": False,
            "playlist": False
        }
    }

def normalize_playlist(item):
    if not item or not isinstance(item, dict):
        return None
    playlist_id = _canonical_playlist_id(item.get("playlistId"))
    if not playlist_id:
        return None

    title = _get_text(item.get("title"))
    subtitle = _get_text(item.get("description")) or ", ".join(_get_text(a.get("name")) for a in item.get("author", []) if a.get("name"))

    return {
        "kind": "playlist",
        "key": f"playlist_{playlist_id}",
        "title": title,
        "subtitle": subtitle,
        "image": _get_best_thumbnail(item.get("thumbnails")),
        "images": item.get("thumbnails", []),
        "playlistId": playlist_id,
        "target": _construct_target("playlist", playlist_id),
        "play": _construct_play("playlist", playlist_id=playlist_id),
        "capabilities": {
            "play": True,
            "like": False,
            "queue": False,
            "playlist": False
        }
    }

def normalize_artist(item):
    if not item or not isinstance(item, dict):
        return None
    browse_id = item.get("browseId")
    if not browse_id:
        return None

    title = _get_text(item.get("title"))
    subtitle = _get_text(item.get("subscribers")) or "Artist"

    return {
        "kind": "artist",
        "key": f"artist_{browse_id}",
        "title": title,
        "subtitle": subtitle,
        "image": _get_best_thumbnail(item.get("thumbnails")),
        "images": item.get("thumbnails", []),
        "browseId": browse_id,
        "target": _construct_target("artist", browse_id),
        "play": _construct_play("artist", playlist_id=item.get("radioId")),
        "capabilities": {
            "play": bool(item.get("radioId")),
            "like": False,
            "queue": False,
            "playlist": False
        }
    }

def normalize_station(item):
    if not item or not isinstance(item, dict):
        return None
    playlist_id = _canonical_playlist_id(item.get("playlistId"))
    if not playlist_id:
        return None

    title = _get_text(item.get("title"))
    subtitle = "Station"

    return {
        "kind": "station",
        "key": f"station_{playlist_id}",
        "title": title,
        "subtitle": subtitle,
        "image": _get_best_thumbnail(item.get("thumbnails")),
        "images": item.get("thumbnails", []),
        "playlistId": playlist_id,
        "target": None, # Station usually just plays, no specific page in music box
        "play": _construct_play("station", playlist_id=playlist_id),
        "capabilities": {
            "play": True,
            "like": False,
            "queue": False,
            "playlist": False
        }
    }

def normalize_home_item(item):
    """Normalize one item from YT Music's potentially mixed home shelves."""
    if not item or not isinstance(item, dict):
        return None
    # Different home sources use both ytmusicapi's videoId and the app's
    # normalized video_id.  Song identity must win over playlist metadata.
    if item.get("videoId") or item.get("video_id") or item.get("type") in {"song", "track"}:
        return normalize_track(item)
    if item.get("browseId") and "subscribers" in item:
        return normalize_artist(item)
    if item.get("playlistId"):
        if _canonical_playlist_id(item.get("playlistId")).startswith("RD"):
            return normalize_station(item)
        return normalize_playlist(item)
    if item.get("browseId"):
        return normalize_album(item)
    return None

def normalize_local_history(item):
    if not item or not isinstance(item, dict):
        return None
    video_id = item.get("video_id") or item.get("videoId")
    if not video_id:
        return None
    title = item.get("title", "")
    artist = item.get("artist", "")
    thumbnail = item.get("thumbnail_url") or item.get("thumbnail", "")
    
    return {
        "kind": "track",
        "key": f"history_track_{video_id}",
        "title": title,
        "subtitle": artist,
        "image": thumbnail,
        "images": [{"url": thumbnail}] if thumbnail else [],
        "videoId": video_id,
        "target": _construct_target("track", video_id),
        "play": _construct_play("track", video_id=video_id),
        "capabilities": {
            "play": True,
            "like": True,
            "queue": True,
            "playlist": True
        }
    }

def normalize_local_playlist(item):
    if not item or not isinstance(item, dict):
        return None
    pl_id = item.get("id")
    if not pl_id:
        return None
        
    thumbnail_url = ""
    tracks = item.get("tracks", [])
    if tracks and len(tracks) > 0:
        thumbnail_url = tracks[0].get("thumbnail", "")

    return {
        "kind": "playlist",
        "key": f"local_playlist_{pl_id}",
        "title": item.get("name", ""),
        "subtitle": "Local Playlist",
        "image": thumbnail_url,
        "images": [{"url": thumbnail_url}] if thumbnail_url else [],
        "playlistId": pl_id,
        "target": _construct_target("playlist", pl_id),
        "play": _construct_play("playlist", playlist_id=pl_id),
        "capabilities": {
            "play": True,
            "like": False,
            "queue": False,
            "playlist": False
        }
    }


def _build_shelf(shelf_id, title, layout, source_name, items, filters=None):
    if not items or len(items) < MIN_ITEMS_PER_SHELF:
        return None
    items = items[:MAX_ITEMS_PER_SHELF]
    # actions
    # Bulk playback is only meaningful for a homogeneous track shelf. Mixed
    # shelves can contain playlist/album cards whose play target is a whole
    # collection, so showing "Play all" there is misleading.
    play_all = bool(items) and all(
        i.get('kind') == 'track' and (i.get('play') or {}).get('videoId')
        for i in items
    )
    
    return {
        "id": shelf_id,
        "title": title,
        "subtitle": "",
        "layout": layout,
        "source": source_name,
        "actions": {"playAll": play_all, "showAll": False},
        "filters": filters or ["all"],
        "items": items
    }

def assemble_home_feed(sources):
    """
    Policy: local-personal -> authenticated-native -> authenticated-library -> seeded-radio -> public-discovery
    sources is a dict containing raw results from source endpoints.
    """
    shelves = []
    seen_shelf_ids = set()
    global_item_keys = set()
    filters = set(["all"])
    
    def add_shelf(shelf_id, title, layout, source_name, raw_items, normalizer_func, shelf_filters=None):
        if shelf_id in seen_shelf_ids:
            return
        normalized = []
        for ri in raw_items:
            ni = normalizer_func(ri)
            if ni:
                # Dedupe inside shelf based on specific entity id/type
                dedupe_key = ni['key']
                if dedupe_key not in [i['key'] for i in normalized]:
                    normalized.append(ni)
        
        # Optionally global cross-shelf dedupe? 
        # "controlled cross-shelf repetition": some dupes allowed across semantically different shelves.
        # But let's avoid duping entire shelves.
        shelf = _build_shelf(shelf_id, title, layout, source_name, normalized, shelf_filters)
        if shelf:
            shelves.append(shelf)
            seen_shelf_ids.add(shelf_id)
            if shelf_filters:
                filters.update(shelf_filters)

    # 1. Local personal
    local_history = sources.get('local_history', [])
    if local_history:
        add_shelf('listen-again', 'Listen again', 'song_grid', 'local_history', local_history, normalize_local_history)
        
    local_playlists = sources.get('local_playlists', [])
    if local_playlists:
        add_shelf('saved-playlists', 'Saved playlists', 'cards', 'local_playlists', local_playlists, normalize_local_playlist)

    # 2. YTM Home native (mix of shelves)
    ytm_home = sources.get('ytm_home', [])
    if isinstance(ytm_home, list):
        for idx, ytm_shelf in enumerate(ytm_home):
            if not isinstance(ytm_shelf, dict):
                continue
            title = _get_text(ytm_shelf.get('title'))
            contents = ytm_shelf.get('contents', [])
            if not contents:
                continue
            
            # Home shelves can mix tracks, playlists and stations (notably
            # "Listen again"). Normalize each item independently, then choose
            # a layout that can represent every entity in the shelf.
            normalized_contents = [normalize_home_item(item) for item in contents]
            normalized_contents = [item for item in normalized_contents if item]
            kinds = {item['kind'] for item in normalized_contents}
            if kinds == {'track'}:
                layout = 'song_grid'
            elif kinds == {'artist'}:
                layout = 'circles'
            else:
                layout = 'cards'

            shelf_id = hashlib.md5(title.encode('utf-8')).hexdigest()[:8]
            add_shelf(
                f"ytm_{shelf_id}", title, layout, 'ytmusic_home',
                normalized_contents, lambda item: item
            )

    
    # filters formatting
    filter_list = [{"id": f, "label": f.capitalize()} for f in sorted(list(filters))]
    
    return {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": int(time.time()),
        "partial": False,
        "stale": False,
        "filters": filter_list,
        "shelves": shelves[:MAX_SHELVES]
    }
