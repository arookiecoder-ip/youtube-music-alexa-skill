import concurrent.futures
import threading
from liked_songs_cache import LikedSongsCache


def test_cold_requests_share_fetch_and_fresh_cache():
    cache = LikedSongsCache()
    release, started = threading.Event(), threading.Event()
    calls = []
    def load():
        calls.append(1); started.set(); assert release.wait(2)
        return ['song', 'song']
    try:
        with concurrent.futures.ThreadPoolExecutor(4) as callers:
            pending = [callers.submit(cache.get, load) for _ in range(4)]
            assert started.wait(2)
            release.set()
            assert all(f.result(2)[0] == ['song'] for f in pending)
        assert len(calls) == 1
        assert cache.get(lambda: (_ for _ in ()).throw(AssertionError())) == (['song'], False)
    finally:
        cache.close()


def test_stale_reads_return_immediately_and_keep_favourites_on_failure():
    now = [0]
    cache = LikedSongsCache(clock=lambda: now[0])
    try:
        assert cache.get(lambda: ['liked'])[0] == ['liked']
        now[0] = 61
        def fail():
            raise RuntimeError('YouTube offline')
        assert cache.get(fail) == (['liked'], True)
        cache._flight.result(2)
        assert cache.get(fail) == (['liked'], True)
    finally:
        cache.close()


def test_like_during_refresh_cannot_be_overwritten_by_old_result():
    now = [0]
    cache = LikedSongsCache(clock=lambda: now[0])
    started, release = threading.Event(), threading.Event()
    try:
        cache.get(lambda: ['old'])
        now[0] = 61
        def load():
            started.set(); assert release.wait(2); return ['old']
        assert cache.get(load)[1]
        assert started.wait(2)
        cache.update('new', True)
        cache.update('old', False)
        release.set()
        cache._flight.result(2)
        assert cache.get(lambda: ['wrong'])[0] == ['new']
    finally:
        release.set(); cache.close()


def test_account_change_fences_old_inflight_data():
    cache = LikedSongsCache()
    started, release = threading.Event(), threading.Event()
    def load():
        started.set(); assert release.wait(2); return ['old-account']
    try:
        with concurrent.futures.ThreadPoolExecutor(1) as caller:
            pending = caller.submit(cache.get, load)
            assert started.wait(2)
            cache.clear()
            release.set()
            try:
                pending.result(2)
                assert False, 'Old account result must fail'
            except RuntimeError:
                pass
        assert cache.get(lambda: ['new-account'])[0] == ['new-account']
    finally:
        release.set(); cache.close()


def test_cold_failure_has_a_bounded_retry_window():
    now, calls = [0], []
    cache = LikedSongsCache(clock=lambda: now[0])
    def fail():
        calls.append(1)
        raise RuntimeError('offline')
    try:
        for _ in range(3):
            try:
                cache.get(fail)
                assert False
            except RuntimeError:
                pass
        assert len(calls) == 1
        now[0] = 16
        assert cache.get(lambda: ['recovered'])[0] == ['recovered']
    finally:
        cache.close()
