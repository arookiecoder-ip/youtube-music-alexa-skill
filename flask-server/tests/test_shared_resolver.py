"""One resolver worker per cookie jar, not per account.

The cold-play handshake depends on the credential, not on the person, so every
identity downloading with the server jar must reach the same warm worker. What
must never be shared is playback state (each facade keeps its own), and deleting
one account must never stop a worker the rest of the install is using.
"""
import os
import unittest
from unittest import mock
from test_next_track import server

SHARED = '/data/download_cookies_server.txt'
ACCOUNT = '/data/download_cookies_account.txt'


class ResolverPoolSharing(unittest.TestCase):
    def setUp(self):
        self.pool = server._YouTubeResolverPool(limit=2)

    def test_accounts_on_one_jar_share_a_worker(self):
        self.assertIs(self.pool.get(SHARED), self.pool.get(SHARED))
        self.assertIs(self.pool.get(SHARED), self.pool.get(os.path.relpath(SHARED)))

    def test_a_different_jar_gets_its_own_worker(self):
        self.assertIsNot(self.pool.get(ACCOUNT), self.pool.get(SHARED))

    def test_without_a_jar_everyone_shares_the_anonymous_worker(self):
        self.assertIs(self.pool.get(None), self.pool.get(''))

    def test_discarding_one_jar_leaves_the_other_workers_alone(self):
        own, shared = self.pool.get(ACCOUNT), self.pool.get(SHARED)
        with mock.patch.object(own, 'close') as closed:
            self.assertTrue(self.pool.discard(ACCOUNT))
        closed.assert_called_once()
        self.assertIs(self.pool.get(SHARED), shared)
        self.assertIsNot(self.pool.get(ACCOUNT), own)

    def test_eviction_closes_only_the_least_recently_used_worker(self):
        first, second = self.pool.get('/data/a.txt'), self.pool.get('/data/b.txt')
        with mock.patch.object(first, 'close') as closed:
            third = self.pool.get('/data/c.txt')
        closed.assert_called_once()
        self.assertIs(self.pool.get('/data/b.txt'), second)
        self.assertIs(self.pool.get('/data/c.txt'), third)


class ServerWiring(unittest.TestCase):
    def test_two_identities_on_the_server_jar_share_the_resolver(self):
        resolvers = server._YouTubeResolverPool(limit=2)
        with mock.patch.object(server, '_youtube_resolvers', resolvers), \
                mock.patch.object(server.Supporting, 'get_ytdlp_cookies_file',
                                  return_value=SHARED):
            self.addCleanup(server._youtube_streams.discard, 'user:shared-a')
            self.addCleanup(server._youtube_streams.discard, 'user:shared-b')
            first = server._youtube_stream_for('user:shared-a')
            second = server._youtube_stream_for('user:shared-b')
            shared = first._active_resolver()
            self.assertIs(shared, second._active_resolver())
            self.assertIsNot(first.current, second.current)
            self.assertIsNot(first.slots, second.slots)
            with mock.patch.object(shared, 'close') as closed:
                server._youtube_streams.discard('user:shared-a')
                server._youtube_streams.discard('user:shared-b')
            closed.assert_not_called()

    def test_deleting_an_account_drops_only_its_own_jar_worker(self):
        resolvers = server._YouTubeResolverPool(limit=2)
        with mock.patch.object(server, '_youtube_resolvers', resolvers), \
                mock.patch.object(server, '_download_cookie_path', return_value=ACCOUNT), \
                mock.patch.object(server, '_server_download_cookie_path', return_value=SHARED):
            own, shared = resolvers.get(ACCOUNT), resolvers.get(SHARED)
            with mock.patch.object(own, 'close') as own_closed, \
                    mock.patch.object(shared, 'close') as shared_closed:
                server._discard_account_resolver('user:account')
            own_closed.assert_called_once()
            shared_closed.assert_not_called()


if __name__ == '__main__':
    unittest.main()
