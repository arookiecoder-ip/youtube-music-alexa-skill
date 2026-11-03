"""One capability and one serial command boundary for the physical browser."""
from contextlib import contextmanager
from contextvars import ContextVar
import secrets
import threading
import time

OWNER = ContextVar('browser_owner', default=None)
ATTEMPT = ContextVar('browser_attempt', default=None)


@contextmanager
def browser_scope(owner, attempt=None):
    a, b = OWNER.set(str(owner or 'owner')), ATTEMPT.set(attempt)
    try:
        yield
    finally:
        OWNER.reset(a)
        ATTEMPT.reset(b)


class BrowserBroker:
    def __init__(self):
        self.lock = threading.RLock()
        self.owner, self.token, self.expires = None, None, 0
        self.previous = None

    def command(self, owner, path, expected, send):
        with self.lock:
            now = time.monotonic()
            starting = path in ('/interactive/start', '/refresh', '/google/start')
            if starting:
                if self.token and self.expires > now and self.owner != owner:
                    return {'state': 'busy', 'outcome': 'browser_in_use'}
                self.previous = self.token
                self.owner, self.token, self.expires = owner, secrets.token_urlsafe(32), now + 300
            elif path not in ('/health', '/profile/delete'):
                if self.owner != owner or not self.token or now >= self.expires or (expected and expected != self.token):
                    raise PermissionError('Browser sign-in attempt expired')
            token = self.token
            result = send(token)
            if self.token != token:
                raise PermissionError('Browser sign-in attempt changed')
            if isinstance(result, dict):
                result = dict(result, attempt_id=token)
            if path in ('/interactive/close', '/interactive/complete', '/google/stop'):
                self.owner, self.token, self.expires = None, None, 0
            elif self.token:
                self.expires = time.monotonic() + 300
            return result


BROKER = BrowserBroker()
