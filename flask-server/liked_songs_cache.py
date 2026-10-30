"""One bounded owner-account cache; stale reads never wait for YouTube.

An invalidation generation prevents an in-flight pre-like fetch from restoring
obsolete favourites. Only one refresh runs at a time, including cold reads.
"""
import concurrent.futures
import threading
import time


class LikedSongsCache:
    def __init__(self, ttl=60, clock=time.monotonic):
        self.ttl, self.clock = ttl, clock
        self._lock = threading.Lock()
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix='liked-refresh')
        self._value = None
        self._expires = 0
        self._generation = 0
        self._flight = None

    def invalidate(self):
        with self._lock:
            self._generation += 1
            self._expires = 0

    def clear(self):
        with self._lock:
            self._generation += 1
            self._value = None
            self._expires = 0

    def update(self, video_id, liked):
        with self._lock:
            self._generation += 1
            if self._value is not None:
                self._value = [v for v in self._value if v != video_id]
                if liked:
                    self._value.append(video_id)
                self._expires = self.clock() + self.ttl

    def get(self, load, timeout=12):
        with self._lock:
            if self._value is not None and self.clock() < self._expires:
                return list(self._value), False
            if self._flight is None or self._flight.done():
                generation = self._generation
                self._flight = self._executor.submit(self._refresh, load, generation)
                # Stale refresh errors are intentionally handled by _refresh;
                # every Future is consumed by a caller or the next refresh.
            flight = self._flight
            if self._value is not None:
                return list(self._value), True
        return list(flight.result(timeout=timeout)), False

    def _refresh(self, load, generation):
        try:
            value = list(dict.fromkeys(load()))
        except Exception:
            with self._lock:
                # Bound retries during an upstream outage; preserve favourites.
                self._expires = self.clock() + min(15, self.ttl)
                if self._value is not None:
                    return list(self._value)
            raise
        with self._lock:
            if generation == self._generation:
                self._value, self._expires = value, self.clock() + self.ttl
            elif self._value is None:
                raise RuntimeError('Account changed during liked-song refresh')
            return list(self._value)

    def close(self):
        self._executor.shutdown(wait=True, cancel_futures=True)
