"""Bounded credential resolver and account relay registries."""
import collections
import logging
import os
import threading
from player_state import current as current_identity
from youtube_stream import Resolver, StreamError
logger = logging.getLogger(__name__)

class _YouTubeStreamPool:
    """One relay facade per identity: its stream slots and current-track bytes.

    The expensive resolver worker is shared per credential (see
    ``_YouTubeResolverPool``); what stays account-scoped here is the playback
    state -- the slot budget and the bytes already relayed to this account."""
    def __init__(self, factory, limit=4, enabled=lambda: False, global_limit=8):
        self._factory = factory
        self._enabled = enabled
        self.global_limit = global_limit
        self._limit = max(1, int(limit))
        self._streams = collections.OrderedDict()
        self._lock = threading.Lock()

    def get(self, identity=None):
        identity = identity or current_identity()
        with self._lock:
            stream = self._streams.get(identity)
            if stream is None:
                stream = self._streams[identity] = self._factory(identity)
            self._streams.move_to_end(identity)
            while len(self._streams) > self._limit:
                _, evicted = self._streams.popitem(last=False)
                try:
                    evicted.close()
                except Exception:
                    logger.debug('Evicted YouTube relay did not close cleanly')
            return stream

    def every(self):
        with self._lock:
            return list(self._streams.values())

    def discard(self, identity):
        """Close and drop one identity's relay facade.

        Eviction alone never finishes a deletion: the pool holds at most a few
        facades and a deleted account's could sit at the warm end indefinitely.
        The shared resolver worker is deliberately left running because other
        accounts are playing through it; a worker built on this account's own
        fallback jar is dropped separately by ``_discard_account_resolver``."""
        with self._lock:
            stream = self._streams.pop(identity, None)
        if stream is None:
            return False
        try:
            stream.close()
        except Exception:
            logger.debug('Discarded YouTube relay did not close cleanly')
        return True

    @property
    def enabled(self):
        return self._enabled()

    def snapshot(self):
        """Aggregate view of every live relay, counting each resolver once."""
        streams, resolving, slots, resolvers = [], 0, 0, set()
        for stream in self.every():
            data = stream.snapshot()
            streams.extend(data.get('streams') or [])
            slots += data.get('stream_slots', 0)
            resolver_id = data.get('resolver_id')
            if resolver_id is not None and resolver_id not in resolvers:
                resolvers.add(resolver_id)
                resolving += data.get('resolving', 0)
        return {'active_streams': len(streams), 'stream_slots': slots,
                'resolving': resolving, 'streams': streams, 'workers': len(resolvers),
                'global_stream_slots': self.global_limit, 'available_global_streams': max(0, self.global_limit-len(streams))}


class _YouTubeResolverPool:
    """One resolver worker per download credential.

    The worker owns the expensive part -- a persistent Node process, its
    Innertube session and its resolved-URL cache -- and it depends only on the
    cookie jar it signs with. Accounts that download with the same jar (normally
    the server's) therefore share it: a cold song costs one request, not one
    sign-in per account. A different jar still gets its own worker, because a
    URL signed with one credential must never be handed to another.
    """
    def __init__(self, limit=4):
        self._limit = max(1, int(limit))
        self._resolvers = collections.OrderedDict()
        self._lock = threading.Lock()

    @staticmethod
    def _key(cookie_file):
        return os.path.abspath(cookie_file) if cookie_file else ''

    def get(self, cookie_file):
        key = self._key(cookie_file)
        with self._lock:
            resolver = self._resolvers.get(key)
            if resolver is None:
                if len(self._resolvers) >= self._limit:
                    for old_key, old in list(self._resolvers.items()):
                        if old.retire_if_idle():
                            self._resolvers.pop(old_key)
                            break
                    else:
                        raise StreamError('busy')
                path = cookie_file or None
                resolver = self._resolvers[key] = Resolver(lambda: path)
            self._resolvers.move_to_end(key)
            return resolver

    def discard(self, cookie_file):
        """Close the worker built on a jar nobody should use any more."""
        key = self._key(cookie_file)
        with self._lock:
            resolver = self._resolvers.pop(key, None)
        if resolver is None:
            return False
        try:
            resolver.close()
        except Exception:
            logger.debug('Discarded YouTube resolver did not close cleanly')
        return True
