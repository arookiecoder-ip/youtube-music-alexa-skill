import threading
import unittest

from audio_jobs import AudioJobs


class AudioJobsTests(unittest.TestCase):
    def setUp(self):
        self.pool = AudioJobs(workers=2, capacity=4, prefetch_workers=1, prefetch_capacity=2)
        self.release = threading.Event()
        self.addCleanup(self.pool.close)
        self.addCleanup(self.release.set)

    def blocked(self, started):
        started.set()
        self.assertTrue(self.release.wait(3), 'test worker timed out')
        return True

    def test_duplicate_requests_share_one_job_and_future(self):
        started = threading.Event()
        first = self.pool.submit('song', lambda: self.blocked(started))
        self.assertTrue(started.wait(1))
        duplicate = self.pool.submit('song', lambda: self.fail('duplicate ran'))
        self.assertIs(first, duplicate)
        self.release.set()
        self.assertTrue(first.result(2))
        self.assertFalse(self.pool.contains('song'))
        self.assertEqual(self.pool.snapshot()['coalesced'], 1)

    def test_job_runs_with_the_identity_that_submitted_it(self):
        """A pool thread must resolve audio as its caller, not as the owner."""
        from player_state import IDENTITY
        seen = {}

        def record(name):
            def action():
                seen[name] = IDENTITY.get()
                return True
            return action

        def submit_as(name, identity):
            """Submit from a thread carrying *identity*, the way a request does."""
            box = []
            def runner():
                IDENTITY.set(identity)
                box.append(self.pool.submit((identity, name), record(name)))
            thread = threading.Thread(target=runner)
            thread.start()
            thread.join(3)
            return box[0]

        one, two = 'user:' + '1' * 36, 'user:' + '2' * 36
        first = submit_as('one', one)
        second = submit_as('two', two)
        self.assertTrue(first.result(3))
        self.assertTrue(second.result(3))
        self.assertEqual(seen, {'one': one, 'two': two})

    def test_same_video_for_two_identities_is_not_one_job(self):
        video = 'AAAAAAAAAAA'
        owner = self.pool.submit(('owner', video), lambda: 'owner-url')
        account = self.pool.submit(('user:abc', video), lambda: 'account-url')
        self.assertEqual(owner.result(3), 'owner-url')
        self.assertEqual(account.result(3), 'account-url')

    def test_prefetch_leaves_worker_available_for_requested_play(self):
        started = threading.Event()
        self.pool.submit('prefetch', lambda: self.blocked(started), prefetch=True)
        self.assertTrue(started.wait(1))
        self.pool.submit('queued-prefetch', lambda: self.blocked(threading.Event()), prefetch=True)
        foreground = self.pool.submit('requested', lambda: 'ready')
        self.assertEqual(foreground.result(1), 'ready')
        self.assertEqual(self.pool.snapshot()['prefetch_active'], 1)

    def test_saturation_rejects_without_creating_more_workers(self):
        a, b = threading.Event(), threading.Event()
        self.pool.submit('a', lambda: self.blocked(a))
        self.pool.submit('b', lambda: self.blocked(b))
        self.assertTrue(a.wait(1) and b.wait(1))
        self.pool.submit('c', lambda: True)
        self.pool.submit('d', lambda: True)
        self.assertIsNone(self.pool.submit('overflow', lambda: True))
        snapshot = self.pool.snapshot()
        self.assertEqual((snapshot['active'], snapshot['queued']), (2, 2))
        self.assertEqual(snapshot['rejected'], 1)

    def test_foreground_promotes_queued_prefetch_and_replaces_stale_action(self):
        a, b = threading.Event(), threading.Event()
        self.pool.submit('a', lambda: self.blocked(a))
        self.pool.submit('b', lambda: self.blocked(b))
        self.assertTrue(a.wait(1) and b.wait(1))
        old = self.pool.submit('next', lambda: self.fail('stale generation ran'), prefetch=True)
        promoted = self.pool.submit('next', lambda: 'requested')
        self.assertIs(old, promoted)
        self.release.set()
        self.assertEqual(promoted.result(2), 'requested')
        self.assertEqual(self.pool.snapshot()['promoted'], 1)

    def test_failure_releases_capacity_and_can_retry(self):
        def fail():
            raise RuntimeError('download failed')
        with self.assertRaises(RuntimeError):
            self.pool.submit('song', fail).result(1)
        self.assertEqual(self.pool.submit('song', lambda: 'retry').result(1), 'retry')
        self.assertEqual(self.pool.snapshot()['failed'], 1)

    def test_close_cancels_pending_and_rejects_new_jobs(self):
        a, b = threading.Event(), threading.Event()
        self.pool.submit('a', lambda: self.blocked(a))
        self.pool.submit('b', lambda: self.blocked(b))
        self.assertTrue(a.wait(1) and b.wait(1))
        pending = self.pool.submit('pending', lambda: self.fail('closed job ran'))
        self.pool.close()
        self.assertTrue(pending.cancelled())
        self.assertIsNone(self.pool.submit('new', lambda: True))

    def test_diagnostics_do_not_retain_identifiers_or_results(self):
        self.pool.submit('private-song', lambda: 'private-path').result(1)
        self.assertNotIn('private', str(self.pool.snapshot()))
        self.assertEqual(self.pool.snapshot()['completed'], 1)


class AudioJobIntegrationTests(unittest.TestCase):
    def test_repeated_prepare_calls_share_one_url_resolution(self):
        import asyncio
        from unittest import mock
        from test_next_track import server
        pool = AudioJobs(workers=2, capacity=4, prefetch_workers=1)
        release, started = threading.Event(), threading.Event()
        def download(*args, **kwargs):
            started.set()
            release.wait(3)
            return {'url':'https://rr1.googlevideo.com/audio'}
        try:
            with mock.patch.object(server, '_audio_jobs', pool), \
                 mock.patch.object(server._youtube_stream_for(), 'resolve', side_effect=download) as fetch, \
                 mock.patch.object(server, 'PUBLIC_BASE_URL', 'https://audio.example'):
                server._ensure_audio_ready_for_play('AAAAAAAAAAA')
                self.assertTrue(started.wait(1))
                for _ in range(50):
                    server._ensure_audio_ready_for_play('AAAAAAAAAAA')
                stream = asyncio.run(server.Supporting.get_stream('AAAAAAAAAAA'))
                self.assertIn('/proxy/', stream['audio_url'])
                self.assertTrue(server._download_in_progress('AAAAAAAAAAA'))
                self.assertEqual(fetch.call_count, 1)
                future = pool.submit(server._audio_job_key('AAAAAAAAAAA'), lambda: None)
                release.set()
                self.assertEqual(future.result(2), {'url':'https://rr1.googlevideo.com/audio'})
        finally:
            release.set()
            pool.close()
