"""Nonblocking batches of redacted journal errors using the configured admin SMTP."""
from collections import OrderedDict
from datetime import datetime, timezone
import hashlib
import queue
import threading
import time


class ErrorAlerts:
    def __init__(self, deliver, *, interval=60, capacity=256, start=True):
        self.deliver, self.interval = deliver, interval
        self.pending = queue.Queue(capacity)
        self.groups = OrderedDict()
        self.dropped = 0
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.failures = 0
        self.last_delivery = ''
        if start:
            threading.Thread(target=self._run, name='error-email-alerts', daemon=True).start()

    def submit(self, row):
        # Rows are already redacted by Journal; raw records never enter this queue.
        if not row[-1]:
            return
        try:
            self.pending.put_nowait(row)
        except queue.Full:
            with self.lock:
                self.dropped += 1

    def flush(self):
        while True:
            try:
                row = self.pending.get_nowait()
            except queue.Empty:
                break
            ident, at, level, source, message, trace, request_id, route, _ = row
            key = hashlib.sha256(repr((level, source, message, trace, route)).encode()).hexdigest()
            if key in self.groups:
                group = self.groups[key]
                group['count'] += 1
                group['last'] = at
            elif len(self.groups) < 128:
                self.groups[key] = dict(id=ident, first=at, last=at, level=level, source=source,
                    message=message, trace=trace, request_id=request_id, route=route, count=1)
            else:
                with self.lock:
                    self.dropped += 1
        if not self.groups:
            return
        with self.lock:
            dropped = self.dropped
        lines = ['Music Box server errors', 'Times are UTC. Sensitive values are redacted.', '']
        for group in self.groups.values():
            first = datetime.fromtimestamp(group['first'], timezone.utc).isoformat()
            last = datetime.fromtimestamp(group['last'], timezone.utc).isoformat()
            lines += [f"{group['level']} · {group['source']} · {group['count']} occurrence(s)",
                f"First: {first} | Last: {last}", f"Journal ID: {group['id']}",
                f"Request ID: {group['request_id'] or 'background'} | Route: {group['route'] or 'background'}",
                group['message'], group['trace'], '']
        if dropped:
            lines += [f'{dropped} additional errors exceeded the email buffer. Review the server journal.']
        try:
            # Delivery must not use an application logger (prevents SMTP failure recursion).
            result = self.deliver(f"Music Box: {sum(g['count'] for g in self.groups.values())} server error(s)", '\n'.join(lines))
            self.last_delivery = result.status
            if result.status not in ('sent', 'outbox'):
                self.failures += 1
                return
        except Exception:
            self.failures += 1
            self.last_delivery = 'error'
            return
        self.groups.clear()
        with self.lock:
            self.dropped -= dropped

    def _run(self):
        # Batching bounds SMTP work during outages, while retaining every repeated count.
        while not self.stop.wait(self.interval):
            self.flush()
