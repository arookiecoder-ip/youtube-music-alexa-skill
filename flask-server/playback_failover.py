"""Bound consecutive failed songs without counting duplicate callbacks twice."""
import threading

class PlaybackFailoverBudget:
    def __init__(self, limit=5):
        self.limit = limit
        self._lock = threading.RLock()
        self.reset()

    def reset(self):
        with self._lock:
            self.count = 0
            self._last = None

    def fail(self, occurrence):
        with self._lock:
            if occurrence == self._last:
                return self.count, False
            self._last = occurrence
            self.count += 1
            return self.count, True
