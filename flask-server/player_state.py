"""Per-user playback state (Phase 2.1).

The single-owner server keeps its playback authority in module-level dicts that
hundreds of call sites read and write directly (`_now_playing['queue']`,
`with _np_lock:` ...). Rewriting every site would be a huge, risky diff, so this
module supplies two objects that keep the call sites unchanged while resolving
the *actual* dict/lock from the request's identity:

    IDENTITY        # contextvar: 'owner' or 'user:<uuid>'
    PlayerStateView # behaves like the old dict, backed by the caller's state
    IdentityLock    # behaves like the old thread Lock, per identity

The identity is bound once per request (and per background thread that opts in
by calling ``IDENTITY.set(...)``). Anything that does not bind gets ``owner``,
so the existing owner, Alexa and Jam paths keep their current single-owner
semantics until they are migrated.

Persistence is keyed by the same identity: `PlaybackStore` holds one snapshot row
per identity, and an identity's state is loaded from it when its entry is created.
Anything that does not bind an identity — the owner path, the Alexa skill, threads
that were not started through `spawn()` — still gets `owner`.
"""
from __future__ import annotations

import collections.abc
import contextvars
import threading

IDENTITY = contextvars.ContextVar("musicbox_player_identity", default="owner")
OWNER = "owner"


def current() -> str:
    """The identity this call belongs to ('owner' unless someone bound itself)."""
    return IDENTITY.get() or OWNER


def spawn(key, target, args=(), kwargs=None, name=None):
    """Start a daemon thread that runs `target` under identity `key`.

    `threading.Thread` does not copy the caller's context, so a worker started by
    an account would otherwise read the *owner's* playback state and the owner's
    YouTube/Amazon credentials. Passing the identity explicitly is the only way a
    background job can stay with the user who requested it."""
    def run():
        IDENTITY.set(key or OWNER)
        target(*args, **(kwargs or {}))

    thread = threading.Thread(target=run, name=name, daemon=True)
    thread.start()
    return thread


class PlayerStateRegistry:
    """Lazily created per-identity playback state and its lock.

    The factory is called with the identity, so an entry can restore its own
    durable snapshot instead of every identity starting from the same blank."""

    def __init__(self, factory):
        self._factory = factory
        self._states = {}
        self._locks = {}
        self._guard = threading.Lock()

    @staticmethod
    def current_key() -> str:
        return IDENTITY.get() or OWNER

    def key(self) -> str:
        return self.current_key()

    def state_for(self, key: str):
        with self._guard:
            state = self._states.get(key)
            if state is None:
                state = self._factory(key)
                self._states[key] = state
            return state

    def peek(self, key: str):
        """The state an identity already has, or None. Never creates one.

        Reading through ``state_for`` would resurrect the very entry a purge is
        trying to remove, so deletion asks a different question than playback."""
        with self._guard:
            return self._states.get(key)

    def replace_current(self, state) -> None:
        """Swap the calling identity's live state, leaving every other identity's
        alone. Tests and sign-out reset one player without disturbing the rest."""
        with self._guard:
            self._states[self.current_key()] = state

    def lock_for(self, key: str) -> threading.RLock:
        with self._guard:
            lock = self._locks.get(key)
            if lock is None:
                lock = threading.RLock()
                self._locks[key] = lock
            return lock

    def state(self):
        return self.state_for(self.current_key())

    def lock(self) -> threading.RLock:
        return self.lock_for(self.current_key())

    def known_keys(self) -> list:
        with self._guard:
            return sorted(self._states)

    def discard(self, key: str) -> bool:
        """Forget one identity entirely.

        Deleting an account has to drop its state *and* its lock: leaving the lock
        behind would grow one entry per deleted account forever, and leaving the
        state behind would hand a rebound identity (a re-created account id, or a
        rebuilt owner row) somebody else's queue, output lease or volume.
        """
        with self._guard:
            existed = self._states.pop(key, None) is not None
            self._locks.pop(key, None)
        return existed


class PlayerStateProxy:
    """Attribute access on an object-shaped singleton resolves per identity.

    `_playback_output` is read and written by name in dozens of places, and code
    also assigns to it (`_playback_output.serial = ...`). This keeps that syntax
    while each identity gets its own object, so one account switching to its phone
    cannot move another account's (or the owner's) output card.
    """

    def __init__(self, registry: PlayerStateRegistry):
        self._registry = registry

    def __getattr__(self, name):
        return getattr(self._registry.state(), name)

    def __setattr__(self, name, value):
        if name == "_registry":
            object.__setattr__(self, name, value)
            return
        setattr(self._registry.state(), name, value)

    def __delattr__(self, name):
        delattr(self._registry.state(), name)


class PlayerStateView(collections.abc.MutableMapping):
    """A dict-shaped view onto the current identity's playback state."""

    def __init__(self, registry: PlayerStateRegistry):
        self._registry = registry

    def _state(self) -> dict:
        return self._registry.state()

    def __getitem__(self, key):
        return self._state()[key]

    def __setitem__(self, key, value):
        self._state()[key] = value

    def __delitem__(self, key):
        del self._state()[key]

    def __iter__(self):
        return iter(self._state())

    def __len__(self):
        return len(self._state())

    def __contains__(self, key):
        return key in self._state()

    def get(self, key, default=None):
        return self._state().get(key, default)

    def setdefault(self, key, default=None):
        return self._state().setdefault(key, default)

    def pop(self, key, *args):
        return self._state().pop(key, *args)

    def popitem(self, *args, **kwargs):
        # OrderedDict-backed state (LRU eviction) needs the `last` argument.
        return self._state().popitem(*args, **kwargs)

    def update(self, *args, **kwargs):
        self._state().update(*args, **kwargs)

    def clear(self):
        self._state().clear()

    def copy(self) -> dict:
        state = self._state()
        return {key: (list(value) if isinstance(value, list) else value)
                for key, value in state.items()}

    def items(self):
        return self._state().items()

    def keys(self):
        return self._state().keys()

    def values(self):
        return self._state().values()

    def __repr__(self):
        return f"PlayerStateView({self._registry.current_key()!r})"


class IdentityLock:
    """A `with`-usable lock bound to the current identity."""

    def __init__(self, registry: PlayerStateRegistry):
        self._registry = registry
        self._held = threading.local()

    def _lock(self) -> threading.RLock:
        return self._registry.lock()

    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
        lock = self._lock()
        acquired = lock.acquire(blocking, timeout) if timeout >= 0 else lock.acquire(blocking)
        if acquired:
            self._held.lock = lock
        return acquired

    def release(self):
        lock = getattr(self._held, "lock", None) or self._lock()
        lock.release()
        self._held.lock = None

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.release()
        return False
