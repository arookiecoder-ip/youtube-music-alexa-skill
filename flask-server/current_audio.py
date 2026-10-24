"""One anonymous, process-local audio file, retained only for the current track.

Only bytes already flowing to a player are stored. There is no pre-download,
library, inventory, disk budget or eviction worker. Closing the process releases
the anonymous file even after a crash. Old writers cannot resurrect an evicted
track, and existing readers can safely finish after an ownership change.
"""
from dataclasses import dataclass
import os
import tempfile
import threading


def byte_bounds(value, length):
    if not value:
        return 0, length-1
    import re
    if not re.fullmatch(r'bytes=(?:\d+-\d*|-\d+)', value):
        raise ValueError('Invalid range')
    left, right = value[6:].split('-', 1)
    if left:
        start = int(left)
        end = min(int(right), length-1) if right else length-1
    else:
        count = int(right)
        start, end = max(0, length-count), length-1
        if not count:
            start = length
    if start >= length or end < start:
        raise ValueError('Unsatisfiable range')
    return start, end


@dataclass
class Ticket:
    video: str
    epoch: int
    was_current: bool
    bound: int | None = None


class CachedResponse:
    def __init__(self, descriptor, start, end, length, etag, partial, head, on_error):
        self.descriptor, self.offset, self.remaining = descriptor, start, end-start+1
        self.status = 206 if partial else 200
        self.on_error = on_error
        self.headers = {'Content-Type':'audio/mp4', 'Content-Length':str(self.remaining),
                        'Accept-Ranges':'bytes', 'ETag':etag}
        if partial:
            self.headers['Content-Range'] = f'bytes {start}-{end}/{length}'
        if head:
            self.remaining = 0

    def read1(self, count):
        try:
            data = os.pread(self.descriptor, min(count, self.remaining), self.offset)
            if not data and self.remaining and count:
                raise OSError('Incomplete temporary audio')
        except OSError:
            self.on_error()
            raise
        self.offset += len(data)
        self.remaining -= len(data)
        return data

    def close(self):
        if self.descriptor is not None:
            os.close(self.descriptor)
            self.descriptor = None


class CurrentAudio:
    # A very long song or hostile seek pattern must not fill the disk. This is
    # a per-current-track safety ceiling, not a retained music-cache budget.
    MAX_BYTES = 64 * 1024 * 1024
    MAX_RANGES = 512

    def __init__(self, file_factory=tempfile.TemporaryFile):
        self.lock = threading.RLock()
        self.factory = file_factory
        self.video, self.epoch = '', 0
        self.file, self.etag, self.length = None, '', 0
        self.ranges = []

    def activate(self, video):
        with self.lock:
            if video == self.video:
                return
            self._discard()
            self.video = video or ''

    def _discard(self):
        self.epoch += 1
        if self.file is not None:
            try:
                self.file.close()
            except OSError:
                pass
        self.file, self.etag, self.length, self.ranges = None, '', 0, []

    def discard(self, video, epoch=None):
        with self.lock:
            if self.video == video and (epoch is None or epoch == self.epoch):
                self._discard()

    def close(self):
        self.activate('')

    def ticket(self, video):
        with self.lock:
            return Ticket(video, self.epoch, self.video == video)

    def has(self, video):
        with self.lock:
            return bool(video and video == self.video and self.ranges)

    def write(self, ticket, etag, length, offset, block):
        if not block or offset < 0 or offset+len(block) > length:
            return
        with self.lock:
            if self.video != ticket.video:
                return
            if ticket.bound is None:
                # A stream can become current after its first bytes arrive.
                # It can cross that one transition, never an A -> B -> A cycle.
                if self.epoch != ticket.epoch and not (
                        not ticket.was_current and self.epoch == ticket.epoch+1):
                    return
                ticket.bound = self.epoch
            if ticket.bound != self.epoch:
                return
            if self.etag and (self.etag != etag or self.length != length):
                # Do not combine byte ranges from different representations.
                self._discard()
                ticket.bound = self.epoch
            candidate = sorted(self.ranges+[(offset, offset+len(block))])
            merged = []
            for start, end in candidate:
                if merged and start <= merged[-1][1]:
                    merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
                else:
                    merged.append((start, end))
            if len(merged) > self.MAX_RANGES or sum(b-a for a,b in merged) > self.MAX_BYTES:
                return
            try:
                if self.file is None:
                    self.file = self.factory()
                written = os.pwrite(self.file.fileno(), block, offset)
                if written != len(block):
                    raise OSError('Short cache write')
            except OSError:
                # Disk-full/read-only storage must never stop streaming.
                self._discard()
                return
            self.etag, self.length, self.ranges = etag, length, merged

    def open(self, video, byte_range=None, if_range=None, head=False):
        with self.lock:
            if video != self.video or self.file is None or not self.length:
                return None
            selected = byte_range if not if_range or if_range == self.etag else None
            start, end = byte_bounds(selected, self.length)
            if not head and not any(a <= start and b >= end+1 for a,b in self.ranges):
                return None
            try:
                descriptor = os.dup(self.file.fileno())
            except OSError:
                return None
            epoch = self.epoch
            return CachedResponse(descriptor, start, end, self.length, self.etag, bool(selected), head,
                                  lambda:self.discard(video, epoch))
