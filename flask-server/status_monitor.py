"""Bounded process-local telemetry; never stores URLs, query strings or credentials."""
from collections import Counter, OrderedDict, deque
from contextlib import contextmanager, closing
import copy
import heapq
import hashlib
import re
import math
import os
from pathlib import Path
import shutil
import sqlite3
import threading
import time


AUDIO_EXTENSIONS = frozenset({'.m4a', '.mp4', '.webm', '.mp3', '.opus', '.aac', '.ogg', '.wav', '.flac'})


class Metrics:
    def __init__(self, clock=time.monotonic, wall=time.time):
        self.clock, self.wall = clock, wall
        self.started = wall()
        self._lock = threading.Lock()
        self.requests = Counter()
        self.routes = OrderedDict()
        self.latencies = deque(maxlen=1024)
        self.minutes = OrderedDict()
        self.caches = {}
        self.cache_events = {}
        self.fetches = Counter()
        self.fetch_history = deque(maxlen=60)
        self.active_fetches = {}
        self.events = deque(maxlen=60)
        self.active_requests = 0
        self.sequence = 0

    def begin_request(self):
        with self._lock:
            self.active_requests += 1
        return self.clock()

    def finish_request(self, started, method, route, status):
        elapsed = max(0, (self.clock() - started) * 1000)
        with self._lock:
            self.active_requests = max(0, self.active_requests - 1)
            self.requests['total'] += 1
            self.requests[f'{status // 100}xx'] += 1
            self.latencies.append(elapsed)
            # Registered route patterns only, not arbitrary request paths.
            key = f'{method} {route}'
            if key not in self.routes and len(self.routes) >= 128:
                key = 'OTHER'
            row = self.routes.setdefault(key, {'requests': 0, 'errors': 0, 'total_ms': 0, 'max_ms': 0})
            row['requests'] += 1
            row['errors'] += status >= 400
            row['total_ms'] += elapsed
            row['max_ms'] = max(row['max_ms'], elapsed)
            minute = int(self.wall() // 60) * 60
            bucket = self.minutes.setdefault(minute, Counter())
            bucket['requests'] += 1
            bucket['errors'] += status >= 400
            bucket['total_ms'] += elapsed
            while len(self.minutes) > 60:
                self.minutes.popitem(last=False)
            if status >= 400:
                self.events.appendleft({'at': self.wall(), 'kind': 'http', 'label': key,
                                        'status': status, 'duration_ms': round(elapsed, 1)})

    def cache(self, name, hit, item=None, reason=''):
        with self._lock:
            if name not in self.caches and len(self.caches) >= 16:
                return
            self.caches.setdefault(name, Counter())['hits' if hit else 'misses'] += 1
            key = hashlib.sha256(str(item)[:512].encode()).hexdigest()[:12] if item is not None else None
            video = item if isinstance(item, str) and re.fullmatch(r'[A-Za-z0-9_-]{11}', item) else None
            events = self.cache_events.setdefault(name, deque(maxlen=200))
            self.sequence += 1
            events.appendleft({'id': self.sequence, 'at': self.wall(), 'outcome': 'hit' if hit else 'miss',
                              'key': key, 'video_id': video, 'reason': reason})

    def cache_details(self, name, outcome='', before=None, limit=50):
        with self._lock:
            rows = [dict(row) for row in self.cache_events.get(name, ()) if row['at'] > self.wall()-86400
                    and (not outcome or row['outcome'] == outcome) and (before is None or row['id'] < before)]
            totals = dict(self.caches.get(name, {}))
        return {'entries': rows[:limit], 'retained': len(rows),
                'next_before': rows[limit-1]['id'] if len(rows)>limit else None,
                'scope': 'current_process', 'max_events': 200,
                'totals': totals}

    @contextmanager
    def fetch(self, client, video_id, kind='download'):
        started = self.clock()
        with self._lock:
            self.sequence += 1
            token = self.sequence
            # Only these server-selected metadata fields are retained.
            row = {'client': str(client)[:40], 'video_id': str(video_id)[:16], 'started_at': self.wall(),
                   'mode': 'prefetch' if kind == 'prefetch' else 'download'}
            self.fetches['started'] += 1
            if len(self.active_fetches) < 64:
                self.active_fetches[token] = row
        result = {'ok': False}
        try:
            yield result
        finally:
            with self._lock:
                self.active_fetches.pop(token, None)
                outcome = 'succeeded' if result['ok'] else 'failed'
                self.fetches[outcome] += 1
                event = dict(row, kind='fetch', at=self.wall(), status=outcome,
                             duration_ms=round(max(0, self.clock() - started) * 1000, 1))
                self.events.appendleft(event)
                self.fetch_history.appendleft(event)

    def snapshot(self):
        with self._lock:
            for events in self.cache_events.values():
                while events and events[-1]['at'] <= self.wall()-86400:
                    events.pop()
            latency = sorted(self.latencies)
            def percentile(p):
                return round(latency[max(0, math.ceil(len(latency) * p) - 1)], 1) if latency else None
            caches = [{**dict(value), 'name': key, 'hits': value['hits'], 'misses': value['misses'],
                       'hit_rate': round(value['hits'] / (value['hits'] + value['misses']) * 100, 1)}
                      for key, value in self.caches.items() if value['hits'] + value['misses']]
            routes = [dict(row, route=key, average_ms=round(row['total_ms'] / row['requests'], 1))
                      for key, row in self.routes.items()]
            minute = int(self.wall() // 60) * 60
            history = []
            for at in range(minute - 59 * 60, minute + 1, 60):
                value = self.minutes.get(at, Counter())
                history.append({'at': at, 'requests': value['requests'], 'errors': value['errors'],
                                'average_ms': round(value['total_ms'] / value['requests'], 1) if value['requests'] else None})
            return copy.deepcopy({'since': self.started, 'scope': 'current_process',
                'requests': dict(self.requests, active=self.active_requests, p50_ms=percentile(.5),
                                 p95_ms=percentile(.95), p99_ms=percentile(.99), latency_samples=len(latency)),
                'routes': sorted(routes, key=lambda row: row['requests'], reverse=True),
                'caches': caches, 'fetches': dict(self.fetches), 'recent_fetches': list(self.fetch_history),
                'active_fetches': list(self.active_fetches.values()), 'events': list(self.events), 'history': history})


class CachedCollector:
    """Single-flight local collection. Concurrent viewers receive stale data, never queue a scan."""
    def __init__(self, seconds=10, clock=time.monotonic):
        self.seconds, self.clock = seconds, clock
        self._lock = threading.Lock()
        self._value, self._at = None, float('-inf')

    def get(self, collect):
        if self.clock() - self._at < self.seconds:
            return copy.deepcopy(self._value)
        if not self._lock.acquire(blocking=False):
            return copy.deepcopy(self._value) if self._value is not None else {'available': False, 'reason': 'collection_in_progress'}
        try:
            if self.clock() - self._at >= self.seconds:
                self._value = collect()
                self._at = self.clock()
            return copy.deepcopy(self._value)
        finally:
            self._lock.release()


def file_info(path):
    """Only metadata; no file contents, filenames or absolute paths are returned."""
    try:
        stat = Path(path).stat()
        return {'present': Path(path).is_file(), 'bytes': stat.st_size, 'modified_at': stat.st_mtime}
    except (OSError, TypeError, ValueError):
        return {'present': False, 'bytes': 0, 'modified_at': None}


def scan_audio(directory, pinned=(), limit=20000, time_budget=.15):
    start, now = time.monotonic(), time.time()
    result = {'available': True, 'captured_at': now, 'complete': True, 'files': 0, 'bytes': 0,
              'ready_files': 0, 'ready_bytes': 0, 'partial_files': 0, 'partial_bytes': 0,
              'warmup_files': 0, 'warmup_bytes': 0, 'pinned_files': 0, 'pinned_bytes': 0,
              'metadata_files': 0, 'metadata_bytes': 0, 'errors': 0, 'entries': []}
    newest = []
    try:
        with os.scandir(directory) as entries:
            for index, entry in enumerate(entries):
                if index >= limit or time.monotonic() - start > time_budget:
                    result['complete'] = False
                    break
                try:
                    if not entry.is_file(follow_symlinks=False):
                        continue
                    stat = entry.stat(follow_symlinks=False)
                    suffix = Path(entry.name).suffix.lower()
                    result['files'] += 1
                    result['bytes'] += stat.st_size
                    if suffix == '.warmup':
                        result['metadata_files'] += 1
                        result['metadata_bytes'] += stat.st_size
                        continue  # markers count as metadata below, not audio
                    kind = 'partial' if suffix == '.part' else ('ready' if suffix in AUDIO_EXTENSIONS else 'metadata')
                    result[f'{kind}_files'] += 1
                    result[f'{kind}_bytes'] += stat.st_size
                    if kind == 'metadata':
                        continue
                    video = entry.name.split('.')[0]
                    warm = kind == 'ready' and Path(directory, video + '.warmup').is_file()
                    pin = video in pinned
                    if warm:
                        result['warmup_files'] += 1
                        result['warmup_bytes'] += stat.st_size
                    if pin:
                        result['pinned_files'] += 1
                        result['pinned_bytes'] += stat.st_size
                    # No raw filenames; valid video IDs are the only retained identifiers.
                    safe_id = video if len(video) == 11 and all(c.isascii() and (c.isalnum() or c in '_-') for c in video) else 'unidentified'
                    row = {'video_id': safe_id, 'bytes': stat.st_size, 'format': suffix[1:],
                           'state': kind, 'pool': 'warmup' if warm else 'requested', 'pinned': pin,
                           'modified_at': stat.st_mtime, 'idle_seconds': max(0, now - max(stat.st_mtime, stat.st_atime))}
                    candidate = (stat.st_mtime, index, row)
                    if len(newest) < 200:
                        heapq.heappush(newest, candidate)
                    elif candidate[:2] > newest[0][:2]:
                        heapq.heapreplace(newest, candidate)
                except OSError:
                    result['errors'] += 1
    except FileNotFoundError:
        result['available'] = False
        result['reason'] = 'directory_missing'
    except OSError:
        result['available'] = False
        result['reason'] = 'directory_unreadable'
    result['entries'] = [row for _, _, row in sorted(newest, reverse=True)]
    result['inventory_limited'] = result['ready_files'] + result['partial_files'] > len(result['entries'])
    return result


def system_snapshot(cache_dir, db_path):
    data = {'cpu_count': os.cpu_count(), 'threads': threading.active_count(),
            'load_average': None, 'memory': {}, 'process': {}, 'disk': {}, 'database': file_info(db_path)}
    try:
        data['load_average'] = list(os.getloadavg())
    except (AttributeError, OSError):
        pass
    try:
        memory = {}
        for line in Path('/proc/meminfo').read_text().splitlines():
            key, value = line.split(':', 1)
            if key in {'MemTotal', 'MemAvailable', 'SwapTotal', 'SwapFree'}:
                memory[key] = int(value.split()[0]) * 1024
        data['memory'] = memory
        stat = Path('/proc/self/status').read_text().splitlines()
        data['process']['rss_bytes'] = next((int(line.split()[1]) * 1024 for line in stat if line.startswith('VmRSS:')), None)
    except (OSError, ValueError):
        pass
    # Container limits supplement host-wide /proc data. Do not confuse the two scopes.
    for key, filename in [('memory_limit_bytes', 'memory.max'), ('memory_current_bytes', 'memory.current'), ('cpu_quota', 'cpu.max')]:
        try:
            raw = Path('/sys/fs/cgroup', filename).read_text().strip()
            if key == 'cpu_quota':
                quota, period = raw.split()
                data['process'][key] = None if quota == 'max' else int(quota) / int(period)
            else:
                data['process'][key] = None if raw == 'max' else int(raw)
        except (OSError, ValueError, ZeroDivisionError):
            data['process'][key] = None
    try:
        target = Path(cache_dir).absolute()
        while not target.exists() and target.parent != target:
            target = target.parent
        total, used, free = shutil.disk_usage(target)
        data['disk'] = {'total_bytes': total, 'used_bytes': used, 'free_bytes': free,
                        'used_percent': round(used / total * 100, 1) if total else None}
    except OSError:
        data['disk'] = {'available': False}
    try:
        uri = Path(db_path).absolute().as_uri() + '?mode=ro'
        with closing(sqlite3.connect(uri, uri=True, timeout=.05)) as connection:
            connection.execute('PRAGMA query_only=ON')
            connection.execute('SELECT count(*) FROM sqlite_master').fetchone()
        data['database']['readable'] = True
    except (OSError, ValueError, sqlite3.Error):
        data['database']['readable'] = False
    try:
        times = os.times()
        data['process'].update(cpu_user_seconds=times.user, cpu_system_seconds=times.system)
    except OSError:
        pass
    return data
