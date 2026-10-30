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
        self._listeners = {}

    def version(self):
        with self._lock:
            return self.epoch, self.revision

    def publish(self):
        with self._lock:
            self.revision += 1
            listeners = []
            for event, listener in self._listeners.items():
                if not listener['pending'] and not event.is_set():
                    listener['pending'] = True
                    listeners.append((listener['loop'], event))
        for loop, event in listeners:
            try:
                loop.call_soon_threadsafe(self._wake, event)
            except RuntimeError:  # Gateway shutting down.
                self.unsubscribe(event)

    def _wake(self, event):
        with self._lock:
            listener = self._listeners.get(event)
            if listener is not None:
                listener['pending'] = False
                event.set()

    def subscribe(self):
        loop, event = asyncio.get_running_loop(), asyncio.Event()
        with self._lock:
            self._listeners[event] = {'loop': loop, 'pending': False}
        return event

    def unsubscribe(self, event):
        with self._lock:
            self._listeners.pop(event, None)


playback_events = PlaybackEvents()
