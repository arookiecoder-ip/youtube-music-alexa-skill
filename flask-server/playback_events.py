"""Thread-safe, bounded change notification shared by Flask and the async gateway.

Only revisions cross threads. Consumers fetch a fresh authenticated snapshot;
slow consumers coalesce events rather than accumulating tracks or commands.
"""
import asyncio
import secrets
import threading


class PlaybackEvents:
    def __init__(self):
        self.epoch = secrets.token_hex(16)
        self.revision = 0
        self._lock = threading.Lock()
        self._listeners = set()

    def version(self):
        with self._lock:
            return self.epoch, self.revision

    def publish(self):
        with self._lock:
            self.revision += 1
            listeners = tuple(self._listeners)
        for loop, event in listeners:
            try:
                loop.call_soon_threadsafe(event.set)
            except RuntimeError:  # Gateway shutting down.
                pass

    def subscribe(self):
        loop, event = asyncio.get_running_loop(), asyncio.Event()
        with self._lock:
            self._listeners.add((loop, event))
        return event

    def unsubscribe(self, event):
        with self._lock:
            self._listeners = {pair for pair in self._listeners if pair[1] is not event}


playback_events = PlaybackEvents()
