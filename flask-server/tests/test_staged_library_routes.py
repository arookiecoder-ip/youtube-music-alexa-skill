"""Real route bodies with isolated SQLite; no Google login or live writes."""
import ast
import re
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock
from flask import Flask, jsonify, request
from ytmusicapi.auth.types import AuthType
from local_library import LocalLibrary


class StagedLibraryRouteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.identity = 'user:a'; self.guest = False
        self.client_google = Mock(auth_type=AuthType.UNAUTHORIZED)
        self.app = Flask(__name__)
        self.ns = dict(app=self.app, jsonify=jsonify, request=request, asyncio=__import__('asyncio'),
                       LocalLibrary=LocalLibrary, _get_ytmusic_home=lambda: self.client_google,
                       _local_library=lambda: LocalLibrary(lambda: sqlite3.connect(self.tmp.name+'/db'), self.identity),
                       _current_identity=lambda: self.identity, _has_own_youtube_session=lambda identity: False,
                       _sync_local_library=Mock(), _liked_cache=lambda *args: Mock(), logger=Mock(), _jam_guest=lambda: self.guest,
                       _valid_video_id=lambda value: bool(re.fullmatch(r'[A-Za-z0-9_-]{11}', value or '')),
                       _valid_key_supplied=lambda: False, _get_now_playing=lambda: {}, _bump_liked_version=Mock(),
                       _playlist_tracks_cache_lock=threading.Lock(), _playlist_tracks_cache={},
                       _metadata_from_queue=lambda video: {}, _ensure_db=lambda: None,
                       error_response=lambda message, status: (jsonify(error=message), status))
        names = {'alexa_like','api_get_liked_songs','api_get_library','api_create_library_playlist',
                 'api_add_library_playlist_track','api_get_library_playlist','api_rename_library_playlist',
                 'api_delete_library_playlist','api_subscribed_artists'}
        nodes = [n for n in ast.parse((Path(__file__).parents[1]/'server.py').read_text()).body
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in names]
        exec(compile(ast.Module(body=nodes,type_ignores=[]), 'server.py', 'exec'), self.ns)
        self.client = self.app.test_client()

    def test_unlinked_likes_roundtrip_without_google_mutation(self):
        result = self.client.post('/alexa/like/', json={'video_id':'abcdefghijk','action':'LIKE','title':'My song'})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(self.client.get('/api/liked_songs/').json['liked_songs'], ['abcdefghijk'])
        tracks = self.client.get('/api/library/playlists/LM').json['tracks']
        self.assertEqual(tracks[0]['title'], 'My song')
        self.client_google.rate_song.assert_not_called()
        self.identity = 'user:b'
        self.assertEqual(self.client.get('/api/liked_songs/').json['liked_songs'], [])
        self.assertEqual(self.client.get('/api/library/playlists/LM').json['tracks'], [])

    def test_unlinked_playlist_crud_is_private(self):
        created = self.client.post('/api/library/playlists/', json={'name':'Private list'}).json
        key = created['id']
        self.assertTrue(key.startswith('local_'))
        self.assertEqual(self.client.post(f'/api/library/playlists/{key}/tracks', json={'video_id':'abcdefghijk','title':'Song'}).status_code, 200)
        self.assertEqual(self.client.get(f'/api/library/playlists/{key}').json['tracks'][0]['title'], 'Song')
        self.assertEqual(self.client.patch(f'/api/library/playlists/{key}',json={'title':'Renamed'}).status_code,200)
        self.identity = 'user:b'
        self.assertEqual(self.client.get(f'/api/library/playlists/{key}').status_code,404)
        self.client.delete(f'/api/library/playlists/{key}',json={})
        self.identity = 'user:a'
        self.assertEqual(self.client.get(f'/api/library/playlists/{key}').json['title'],'Renamed')
        self.assertEqual(self.client.delete(f'/api/library/playlists/{key}',json={}).status_code,200)
        self.assertEqual(self.client.get(f'/api/library/playlists/{key}').status_code,404)
        self.client_google.create_playlist.assert_not_called()
        self.client_google.add_playlist_items.assert_not_called()

    def test_guest_cannot_read_local_host_playlist(self):
        key=self.client.post('/api/library/playlists/',json={'name':'Private'}).json['id']
        self.guest=True
        self.assertEqual(self.client.get(f'/api/library/playlists/{key}').status_code,403)
        self.assertEqual(self.client.get('/api/library/playlists/LM').status_code,403)

    def test_unlinked_library_overview_and_subscriptions_are_usable(self):
        self.assertEqual(self.client.get('/api/library/').status_code,200)
        self.assertEqual(self.client.get('/api/subscribed_artists/').json['artists'],[])

    def test_invalid_ids_do_not_enter_staging(self):
        self.assertEqual(self.client.post('/alexa/like/',json={'video_id':'invalid'}).status_code,400)
        self.assertEqual(self.client.get('/api/liked_songs/').json['liked_songs'],[])


class PersonalizedCacheTests(unittest.TestCase):
    def test_liked_playlist_cache_does_not_cross_accounts(self):
        import asyncio
        import time
        from types import SimpleNamespace
        tree = ast.parse((Path(__file__).parents[1]/'server.py').read_text())
        supporting = next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='Supporting')
        method = next(n for n in supporting.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='get_playlist_tracks')
        identity = ['user:a']
        clients = {key: Mock() for key in ('user:a','user:b')}
        clients['user:a'].get_liked_songs.return_value = {'tracks':[{'videoId':'aaaaaaaaaaa','title':'A private like'}]}
        clients['user:b'].get_liked_songs.return_value = {'tracks':[{'videoId':'bbbbbbbbbbb','title':'B private like'}]}
        scope = dict(asyncio=asyncio,time=time,threading=threading,logger=Mock(),
                     _current_identity=lambda:identity[0], _has_own_youtube_session=lambda key:True,
                     _get_ytmusic_home=lambda:clients[identity[0]], _playlist_tracks_cache={},
                     _playlist_tracks_pending={}, _playlist_tracks_cache_lock=threading.Lock(),
                     _PLAYLIST_TRACKS_CACHE_TTL=30, _operational=SimpleNamespace(cache=Mock()),
                     _artist_credit_from_list=lambda artists:'', _artist_entries_from_item=lambda item:[],
                     _valid_video_id=lambda key:len(key or '')==11)
        node = ast.ClassDef(name='Supporting',bases=[],keywords=[],body=[method],decorator_list=[])
        exec(compile(ast.fix_missing_locations(ast.Module(body=[node],type_ignores=[])), 'server.py','exec'),scope)
        scope['Supporting'].duration_ms = lambda track:0
        a = asyncio.run(scope['Supporting'].get_playlist_tracks('LM'))
        identity[0]='user:b'
        b = asyncio.run(scope['Supporting'].get_playlist_tracks('LM'))
        self.assertEqual(a[0]['title'],'A private like')
        self.assertEqual(b[0]['title'],'B private like')
        self.assertEqual(len(scope['_playlist_tracks_cache']),2)

    def test_home_cache_is_bounded_and_reuses_locks_after_eviction(self):
        import collections
        import time
        tree = ast.parse((Path(__file__).parents[1]/'server.py').read_text())
        node = next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='_IdentitySlotCache')
        scope=dict(collections=collections,threading=threading,time=time)
        exec(compile(ast.Module(body=[node],type_ignores=[]),'server.py','exec'),scope)
        cache=scope['_IdentitySlotCache'](maxsize=2)
        lock=cache.lock('user:a'); cache.set('user:a',['private'])
        cache.set('user:b',['other']); cache.set('user:c',['new'])
        self.assertEqual(len(cache._slots),2)
        self.assertIs(cache.lock('user:a'),lock)
        self.assertIsNone(cache.get('user:a')[0])
