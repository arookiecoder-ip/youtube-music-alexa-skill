import sqlite3
import tempfile
import unittest
from unittest.mock import Mock
from local_library import LocalLibrary
from mobile_devices import MobileDevices
from player_state import IDENTITY, PlayerStateRegistry, PlayerStateProxy


class LocalLibraryTests(unittest.TestCase):
    def setUp(self):
        self.file = tempfile.NamedTemporaryFile()
        self.connect = lambda: sqlite3.connect(self.file.name)
        self.a = LocalLibrary(self.connect, 'user:a')
        self.b = LocalLibrary(self.connect, 'user:b')

    def tearDown(self):
        self.file.close()

    def test_likes_and_playlist_operations_are_isolated(self):
        self.a.rate_song('abcdefghijk', 'LIKE')
        key = self.a.create_playlist('Private')
        self.a.add_playlist_items(key, ['abcdefghijk'])
        self.assertEqual(self.b.get_liked_songs()['tracks'], [])
        self.assertEqual(self.b.get_library_playlists(), [])
        with self.assertRaises(ValueError): self.b.get_playlist(key)
        with self.assertRaises(ValueError): self.b.add_playlist_items(key, ['other'])
        self.b.delete_playlist(key)
        self.assertEqual(len(self.a.get_playlist(key)['tracks']), 1)

    def test_confirmed_sync_deletes_only_this_accounts_staging(self):
        for account in (self.a, self.b): account.rate_song('abcdefghijk', 'LIKE')
        key = self.a.create_playlist('Private')
        self.a.add_playlist_items(key, ['abcdefghijk', 'abcdefghijk'])
        client = Mock()
        client.rate_song.return_value = client.add_playlist_items.return_value = {'status': 'STATUS_SUCCEEDED'}
        client.create_playlist.return_value = 'PLremote'
        client.get_playlist.return_value = {'tracks': []}
        self.a.sync(client)
        client.add_playlist_items.assert_called_once_with('PLremote', ['abcdefghijk'], duplicates=False)
        self.assertEqual(self.a.rows('like') + self.a.rows('playlist'), [])
        self.assertEqual(len(self.b.rows('like')), 1)

    def test_retry_retains_failed_items_and_reuses_created_playlist(self):
        key = self.a.create_playlist('Retry')
        self.a.add_playlist_items(key, ['abcdefghijk'])
        client = Mock()
        client.create_playlist.return_value = 'PLremote'
        client.get_playlist.return_value = {'tracks': []}
        client.add_playlist_items.side_effect = RuntimeError('offline')
        with self.assertRaises(RuntimeError): self.a.sync(client)
        self.assertEqual(self.a.get_playlist(key)['remote_id'], 'PLremote')
        client.add_playlist_items.side_effect = None
        client.add_playlist_items.return_value = {'status': 'STATUS_SUCCEEDED'}
        self.a.sync(client)
        client.create_playlist.assert_called_once()
        self.assertEqual(self.a.rows('playlist'), [])

    def test_rejected_rating_is_not_deleted(self):
        self.a.rate_song('abcdefghijk', 'LIKE')
        client = Mock(); client.rate_song.return_value = {'status': 'FAILED'}
        with self.assertRaises(RuntimeError): self.a.sync(client)
        self.assertEqual(len(self.a.rows('like')), 1)

    def test_purge_is_scoped_and_query_uses_primary_key(self):
        for account in (self.a, self.b): account.rate_song('abcdefghijk', 'LIKE')
        self.a.purge()
        self.assertEqual(self.a.rows('like'), [])
        self.assertEqual(len(self.b.rows('like')), 1)
        with self.connect() as conn:
            plan = conn.execute('EXPLAIN QUERY PLAN SELECT id FROM staged_library WHERE identity=? AND kind=?', ('user:b','like')).fetchall()
        self.assertIn('PRIMARY KEY', str(plan))

    def test_presence_proxy_cannot_list_or_control_another_accounts_phone(self):
        registry = PlayerStateRegistry(lambda identity: MobileDevices())
        devices = PlayerStateProxy(registry)
        token = IDENTITY.set('user:a')
        try:
            devices.online('vivo', 'session-a', 'Private vivo', [])
            IDENTITY.set('user:b')
            self.assertEqual(devices.list(), [])
            with self.assertRaises(ValueError): devices.command('vivo', 'token', 'pause', {})
            devices.online('vivo', 'session-b', 'Other vivo', [])
            IDENTITY.set('user:a')
            self.assertEqual(devices.list()[0]['name'], 'Private vivo')
        finally: IDENTITY.reset(token)


class ExpiryMaintenanceTests(unittest.TestCase):
    def test_expiry_cleanup_is_bounded_and_retains_live_rows(self):
        from multiuser.db import Database
        with tempfile.TemporaryDirectory() as directory:
            db = Database(directory + '/accounts.db'); db.initialize()
            with db.connect() as conn:
                for i in range(6):
                    conn.execute('INSERT INTO signup_challenges(token,email,code_enc,expires,sent) VALUES (?,?,?,?,?)',
                                 (str(i), 'test@example.invalid', 'encrypted', 50 if i < 5 else 500, 1))
            self.assertEqual(db.maintain(now=100, batch=2), 2)
            self.assertEqual(db.scalar('SELECT count(*) FROM signup_challenges'), 4)
            db.maintain(now=100); db.maintain(now=100)
            self.assertEqual(db.scalar('SELECT count(*) FROM signup_challenges'), 1)


class ConcurrentSyncTests(unittest.TestCase):
    setUp = LocalLibraryTests.setUp
    tearDown = LocalLibraryTests.tearDown

    def test_rating_changed_during_google_call_is_not_deleted(self):
        self.a.rate_song('abcdefghijk', 'LIKE')
        client = Mock()
        def upstream(*args):
            self.a.rate_song('abcdefghijk', 'INDIFFERENT')
            return {'responseContext': {}}
        client.rate_song.side_effect = upstream
        self.a.sync(client)
        self.assertEqual(self.a.rows('like')[0][1]['rating'], 'INDIFFERENT')

    def test_playlist_added_during_creation_is_preserved_and_uploaded(self):
        key = self.a.create_playlist('List')
        client = Mock()
        def create(*args):
            self.a.add_playlist_items(key, ['abcdefghijk'])
            return 'PLremote'
        client.create_playlist.side_effect = create
        client.get_playlist.return_value = {'tracks': []}
        client.add_playlist_items.return_value = {'status': 'STATUS_SUCCEEDED'}
        self.a.sync(client)
        client.add_playlist_items.assert_called_once_with('PLremote', ['abcdefghijk'], duplicates=False)
        self.assertEqual(self.a.rows('playlist'), [])

    def test_remote_members_are_not_uploaded_again_after_ambiguous_previous_success(self):
        key = self.a.create_playlist('List')
        self.a.add_playlist_items(key, ['abcdefghijk'])
        client = Mock()
        client.create_playlist.return_value = 'PLremote'
        client.get_playlist.return_value = {'tracks':[{'videoId':'abcdefghijk'}]}
        self.a.sync(client)
        client.add_playlist_items.assert_not_called()
        self.assertEqual(self.a.rows('playlist'), [])
