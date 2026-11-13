"""Account-bound staging library. SQLite has no native RLS: every query is scoped.

No provider cookies are accepted here. Sync requires an explicitly supplied client
for this identity; confirmed rows are removed individually so failures are retryable.
"""
import contextlib
import functools
import hashlib
import json
import threading
import uuid


def track_metadata(raw):
    raw = raw if isinstance(raw, dict) else {}
    artists = raw.get('artists') or []
    if not isinstance(artists, list): artists = []
    return {'title': str(raw.get('title') or '')[:500],
            'artist': str(raw.get('artist') or '')[:500],
            'artists': [{'name': str(a.get('name') or '')[:200], 'id': str(a.get('id') or '')[:100]}
                        for a in artists[:20] if isinstance(a, dict)],
            'thumbnail': str(raw.get('thumbnail') or '')[:2000],
            'thumbnails': [{'url': str(raw.get('thumbnail') or '')[:2000]}],
            'duration_ms': max(0, min(86400000, int(raw.get('duration_ms') or 0)))}


_LOCKS = [threading.RLock() for _ in range(64)]


def serialized(method):
    @functools.wraps(method)
    def call(self, *args, **kwargs):
        slot = int.from_bytes(hashlib.sha256(self.identity.encode()).digest()[:2], 'big') % len(_LOCKS)
        with _LOCKS[slot]:
            return method(self, *args, **kwargs)
    return call


class LocalLibrary:
    def __init__(self, connect, identity):
        if not identity or identity == 'server':
            raise ValueError('A user identity is required')
        self.connect, self.identity = connect, identity

    @contextlib.contextmanager
    def db(self):
        conn = self.connect()
        try:
            conn.execute('CREATE TABLE IF NOT EXISTS staged_library ('
                         'identity TEXT NOT NULL, kind TEXT NOT NULL, id TEXT NOT NULL,'
                         'payload TEXT NOT NULL, revision TEXT NOT NULL,'
                         'PRIMARY KEY(identity,kind,id)) WITHOUT ROWID')
            with conn:
                yield conn
        finally:
            conn.close()

    @serialized
    def rows(self, kind):
        with self.db() as conn:
            return [(r[0], json.loads(r[1]), r[2]) for r in conn.execute(
                'SELECT id,payload,revision FROM staged_library WHERE identity=? AND kind=? ORDER BY id',
                (self.identity, kind))]

    def pending(self):
        with self.db() as conn:
            return conn.execute('SELECT 1 FROM staged_library WHERE identity=? LIMIT 1', (self.identity,)).fetchone() is not None

    def keys(self, kind):
        with self.db() as conn:
            return [row[0] for row in conn.execute('SELECT id FROM staged_library WHERE identity=? AND kind=?', (self.identity, kind))]

    def one(self, kind, key):
        with self.db() as conn:
            row = conn.execute('SELECT payload,revision FROM staged_library WHERE identity=? AND kind=? AND id=?', (self.identity,kind,key)).fetchone()
            return (json.loads(row[0]), row[1]) if row else (None, None)

    @serialized
    def put(self, kind, key, value):
        raw = json.dumps(value)
        if len(raw) > 2_000_000:
            raise ValueError('Playlist is too large')
        with self.db() as conn:
            conn.execute('BEGIN IMMEDIATE')
            count = conn.execute('SELECT count(*) FROM staged_library WHERE identity=?', (self.identity,)).fetchone()[0]
            exists = conn.execute('SELECT 1 FROM staged_library WHERE identity=? AND kind=? AND id=?', (self.identity, kind, key)).fetchone()
            playlists = conn.execute("SELECT count(*) FROM staged_library WHERE identity=? AND kind='playlist'", (self.identity,)).fetchone()[0]
            if kind == 'playlist' and playlists >= 100 and not exists:
                raise ValueError('Local playlist limit reached')
            if count >= 5000 and not exists:
                raise ValueError('Local library limit reached')
            conn.execute('INSERT OR REPLACE INTO staged_library VALUES (?,?,?,?,?)',
                         (self.identity, kind, key, raw, uuid.uuid4().hex))

    @serialized
    def delete(self, kind, key, revision=None):
        with self.db() as conn:
            conn.execute('DELETE FROM staged_library WHERE identity=? AND kind=? AND id=?' +
                         (' AND revision=?' if revision else ''),
                         (self.identity, kind, key) + ((revision,) if revision else ()))

    def get_liked_songs(self, limit=1000):
        tracks = [dict(value, videoId=key) for key, value, _ in self.rows('like') if value.get('rating') == 'LIKE']
        return {'tracks': tracks if limit is None else tracks[:limit]}

    @serialized
    def rate_song(self, video_id, rating, metadata=None):
        self.put('like', video_id, dict(track_metadata(metadata), rating=rating))
        return {'status': 'STATUS_SUCCEEDED'}

    def get_library_playlists(self, limit=100):
        with self.db() as conn:
            return [{'playlistId': row[0], 'title': row[1], 'description': row[2], 'remote_id': row[3], 'editable': True}
                    for row in conn.execute(
                        "SELECT id,json_extract(payload,'$.title'),json_extract(payload,'$.description'),json_extract(payload,'$.remote_id') "
                        "FROM staged_library WHERE identity=? AND kind='playlist' ORDER BY id LIMIT ?", (self.identity, min(100, limit)))]

    def get_playlist(self, key=None, limit=100, playlistId=None):
        key = key or playlistId
        value, _ = self.one('playlist', key)
        if value is None:
            raise ValueError('Playlist not found')
        return dict(value, tracks=value['tracks'] if limit is None else value['tracks'][:limit], trackCount=len(value['tracks']))

    @serialized
    def create_playlist(self, title, description='', **kwargs):
        key = 'local_' + uuid.uuid4().hex
        self.put('playlist', key, {'title': title[:200], 'description': description[:2000], 'tracks': []})
        return key

    @serialized
    def add_playlist_items(self, key, video_ids, duplicates=False, metadata=None):
        value = self.get_playlist(key, None)
        seen = {t['videoId'] for t in value['tracks']}
        for video in video_ids:
            if video not in seen:
                value['tracks'].append(dict(track_metadata(metadata), videoId=video))
                seen.add(video)
        if len(value['tracks']) > 5000:
            raise ValueError('Playlist limit reached')
        self.put('playlist', key, value)
        return {'status': 'STATUS_SUCCEEDED'}

    @serialized
    def edit_playlist(self, key, title=None, description=None):
        value = self.get_playlist(key, None)
        if title is not None: value['title'] = title[:200]
        if description is not None: value['description'] = description[:2000]
        self.put('playlist', key, value)

    @serialized
    def delete_playlist(self, key):
        self.delete('playlist', key)

    @serialized
    def purge(self):
        with self.db() as conn:
            conn.execute('DELETE FROM staged_library WHERE identity=?', (self.identity,))

    @serialized
    def remember_remote(self, key, remote):
        value, _ = self.one('playlist', key)
        if value is None:
            return None, None
        value['remote_id'] = remote
        self.put('playlist', key, value)
        return self.one('playlist', key)

    def sync(self, client):
        for key, value, revision in self.rows('like'):
            result = client.rate_song(key, value['rating'])
            if not isinstance(result, dict) or result.get('error') or result.get('status', 'STATUS_SUCCEEDED') != 'STATUS_SUCCEEDED':
                raise RuntimeError('Google did not confirm the rating')
            self.delete('like', key, revision)
        for key in self.keys('playlist'):
            value, revision = self.one('playlist', key)
            if value is None:
                continue
            remote = value.get('remote_id')
            if not remote:
                remote = client.create_playlist(value['title'], value.get('description', ''))
                if not isinstance(remote, str) or not remote.startswith('PL'):
                    raise RuntimeError('Google did not confirm playlist creation')
                value, revision = self.remember_remote(key, remote)
                if value is None:
                    continue
            existing = {t.get('videoId') for t in client.get_playlist(remote, limit=None).get('tracks', [])}
            videos = [t['videoId'] for t in value['tracks'] if t['videoId'] not in existing]
            if videos:
                result = client.add_playlist_items(remote, videos, duplicates=False)
                if not isinstance(result, dict) or result.get('status') != 'STATUS_SUCCEEDED':
                    raise RuntimeError('Google did not confirm playlist items')
            self.delete('playlist', key, revision)
