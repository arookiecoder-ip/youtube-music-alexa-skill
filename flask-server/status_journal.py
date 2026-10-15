"""Bounded, redacted operational logs. Producers never wait for disk or SQLite."""
from contextlib import closing
import logging
import os
from pathlib import Path
import queue
import re
import sqlite3
import threading
import time
import traceback
import uuid

RETENTION = 24 * 60 * 60
LEVELS = {'INFO', 'WARNING', 'ERROR', 'CRITICAL'}


def redact(value, secrets=()):
    text = str(value)[:16000]
    for secret in secrets:
        if isinstance(secret, str) and len(secret) >= 4:
            text = text.replace(secret, '[redacted]')
    # Never retain authentication payloads (including multiline cookie/header dumps).
    if re.search(r'(?i)(cookie|authorization|password|secret|token|api.?key|headers_auth|session[_-]?(?:id|key))', text):
        return '[Authentication-related log content omitted]'
    text = re.sub(r'https?://[^\s\'"<>]+', '[url]', text)
    text = re.sub(r'(?i)\bBearer\s+\S+|\beyJ[A-Za-z0-9_.-]+', '[redacted]', text)
    text = re.sub(r'(?i)(serial|device_id|user_id|account_id|profile_id|client_id)[\s\'":=]+[^\s,;\n}]+', r'\1=[redacted]', text)
    text = re.sub(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}', '[email]', text)
    text = re.sub(r'(?i)(?:[A-Z]:\\|/)(?:[^\s\'"<>:,]+[/\\])+[^\s\'"<>:,]*', '[path]', text)
    text = re.sub(r'[\x00-\x08\x0b-\x1f\x7f]', '', text)
    return text[:2048]


class Journal:
    def __init__(self, path, secrets=lambda: (), clock=time.time, start=True, max_logs=4000, max_errors=1000):
        self.path, self.secrets, self.clock = str(path), secrets, clock
        self.max_logs, self.max_errors = max_logs, max_errors
        self.normal, self.errors = queue.Queue(512), queue.Queue(128)
        self.lock = threading.Lock()
        self.dropped = 0
        self.available = False
        self.conditions = set()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._last_cleanup = 0
        self._initialized = False
        self.worker = None
        if start:
            self.worker = threading.Thread(target=self._run, daemon=True, name='status-journal')
            self.worker.start()

    def _connect(self):
        conn = sqlite3.connect(self.path, timeout=.1)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA busy_timeout=100')
        conn.execute('PRAGMA synchronous=NORMAL')
        conn.execute('PRAGMA journal_size_limit=1048576')
        conn.execute('PRAGMA wal_autocheckpoint=128')
        conn.execute('PRAGMA max_page_count=8192')
        return conn

    def _initialize(self):
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn:
            conn.execute('PRAGMA auto_vacuum=INCREMENTAL')
            conn.execute('PRAGMA journal_mode=WAL')
            conn.execute('''CREATE TABLE IF NOT EXISTS logs (
                seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL,
                at REAL NOT NULL, level TEXT NOT NULL, source TEXT NOT NULL,
                message TEXT NOT NULL, trace TEXT NOT NULL, request_id TEXT NOT NULL,
                route TEXT NOT NULL, is_error INTEGER NOT NULL, seen INTEGER NOT NULL DEFAULT 0)''')
            conn.execute('CREATE INDEX IF NOT EXISTS logs_at ON logs(at)')
            conn.execute('CREATE INDEX IF NOT EXISTS logs_errors ON logs(is_error, seen, seq)')
            conn.commit()
        for suffix in ('', '-wal', '-shm'):
            try: os.chmod(self.path+suffix, 0o600)
            except FileNotFoundError: pass
        self._initialized = True

    def record(self, level, source, message, trace='', request_id='', route=''):
        try:
            secrets = self.secrets()
            frames, text = [], []
            for line in str(trace)[:16000].splitlines():
                (frames if re.fullmatch(r'[A-Za-z0-9_.-]+:\d+ in [A-Za-z0-9_<>.]+', line) else text).append(line)
            safe_trace = redact('\n'.join(text), secrets)
            if frames:
                safe_frames = [frame if not any(isinstance(secret, str) and len(secret)>=4 and secret in frame for secret in secrets) else '[Frame omitted]' for frame in frames[-12:]]
                safe_trace += '\n' + '\n'.join(safe_frames)
            row = (uuid.uuid4().hex, self.clock(), level if level in LEVELS else 'INFO',
                   redact(source, secrets)[:80], redact(message, secrets), safe_trace[:2048],
                   request_id if re.fullmatch(r'[a-f0-9]{32}', request_id) else '',
                   route[:160] if re.fullmatch(r'/[A-Za-z0-9_/< >:.\-]*', route) else '', int(level in ('ERROR', 'CRITICAL')))
            (self.errors if row[-1] else self.normal).put_nowait(row)
            self._wake.set()
        except Exception:
            # The logging path must never break playback or recursively log failures.
            with self.lock: self.dropped += 1

    def condition(self, key, active, message):
        with self.lock:
            was_active = key in self.conditions
            if active: self.conditions.add(key)
            else: self.conditions.discard(key)
        if active and not was_active:
            self.record('ERROR', 'watchdog', message)

    def drain(self):
        if not self._initialized: self._initialize()
        rows = []
        for source in (self.errors, self.normal):
            for _ in range(100):
                try: rows.append(source.get_nowait())
                except queue.Empty: break
        kept = []
        for kind, limit in ((0, self.max_logs), (1, self.max_errors)):
            group = [row for row in rows if row[-1] == kind]
            retained = group[-limit:] if limit else []
            kept.extend(retained)
            with self.lock: self.dropped += len(group) - len(retained)
        rows = sorted(kept, key=lambda row: row[1])
        try:
            with closing(self._connect()) as conn:
                now = self.clock()
                conn.execute('DELETE FROM logs WHERE at <= ?', (now - RETENTION,))
                # Keep independent budgets so an INFO burst cannot evict unseen errors.
                for kind, limit in ((0, self.max_logs), (1, self.max_errors)):
                    conn.execute('DELETE FROM logs WHERE is_error=? AND seq NOT IN (SELECT seq FROM logs WHERE is_error=? ORDER BY seq DESC LIMIT ?)', (kind, kind, max(0, limit - sum(row[-1] == kind for row in rows))))
                conn.executemany('INSERT INTO logs(id,at,level,source,message,trace,request_id,route,is_error) VALUES(?,?,?,?,?,?,?,?,?)', rows)
                conn.commit()
                if now - self._last_cleanup >= 60:
                    conn.execute('PRAGMA incremental_vacuum(256)')
                    conn.execute('PRAGMA wal_checkpoint(PASSIVE)')
                    self._last_cleanup = now
            self.available = True
        except Exception:
            with self.lock: self.dropped += len(rows)
            self.available = False
            raise

    def _run(self):
        while not self._stop.is_set():
            try: self.drain()
            except Exception:
                self.available = False
                self._initialized = False
            if not self.available:
                self._stop.wait(5)  # producers cannot defeat storage-outage backoff
            else:
                self._wake.wait(.05 if not self.normal.empty() or not self.errors.empty() else 60)
            self._wake.clear()

    def close(self):
        self._stop.set(); self._wake.set()
        if self.worker: self.worker.join(timeout=2)

    def summary(self):
        result = {'available': self.available, 'unseen': None, 'retention_seconds': RETENTION,
                  'max_logs': self.max_logs, 'max_errors': self.max_errors, 'dropped': self.dropped, 'as_of': self.clock()}
        if self.available:
            with closing(self._connect()) as conn:
                result['unseen'] = conn.execute('SELECT count(*) FROM logs WHERE is_error=1 AND seen=0 AND at>?', (self.clock()-RETENTION,)).fetchone()[0]
        return result

    def query(self, *, errors=False, level='', source='', search='', before=None, limit=50):
        clauses, args = ['at > ?'], [self.clock()-RETENTION]
        if errors: clauses.append('is_error=1')
        if level: clauses.append('level=?'); args.append(level)
        if source: clauses.append('source=?'); args.append(source)
        if search: clauses.append('(instr(lower(message),lower(?))>0 OR instr(lower(trace),lower(?))>0 OR instr(lower(route),lower(?))>0 OR instr(lower(request_id),lower(?))>0)'); args.extend([search]*4)
        if before is not None: clauses.append('seq < ?'); args.append(before)
        with closing(self._connect()) as conn:
            rows = [dict(row) for row in conn.execute('SELECT * FROM logs WHERE '+' AND '.join(clauses)+' ORDER BY seq DESC LIMIT ?', args+[limit+1])]
            sources = [row[0] for row in conn.execute('SELECT DISTINCT source FROM logs WHERE at>? ORDER BY source LIMIT 50', (self.clock()-RETENTION,))]
        more = len(rows)>limit
        rows = rows[:limit]
        return {'entries': rows, 'next_before': rows[-1]['seq'] if more else None, 'sources': sources, 'summary': self.summary()}

    def detail(self, ident):
        with closing(self._connect()) as conn:
            row = conn.execute('SELECT * FROM logs WHERE id=? AND at>?', (ident,self.clock()-RETENTION)).fetchone()
            if not row or not row['is_error']: return None
            row = dict(row)
            earlier = [dict(item) for item in conn.execute('SELECT * FROM logs WHERE at>? AND seq<? ORDER BY seq DESC LIMIT 10', (self.clock()-RETENTION,row['seq']))]
            later = [dict(item) for item in conn.execute('SELECT * FROM logs WHERE at>? AND seq>? ORDER BY seq ASC LIMIT 10', (self.clock()-RETENTION,row['seq']))]
        # Arrival can lag across threads; show the retained global journal order and request ID.
        return {'entry':row, 'earlier':earlier[::-1], 'later':later}

    def seen(self, ids):
        with closing(self._connect()) as conn:
            conn.executemany('UPDATE logs SET seen=1 WHERE id=? AND is_error=1 AND at>?', [(ident,self.clock()-RETENTION) for ident in ids])
            conn.commit()
        return self.summary()


class JournalHandler(logging.Handler):
    def __init__(self, journal, context=lambda: {}):
        super().__init__(logging.INFO)
        self.journal, self.context = journal, context

    def emit(self, record):
        try:
            # HTTP lines get a separate structured record without URL arguments.
            if record.funcName == '_log_request' or record.name.startswith(('werkzeug', 'urllib3')):
                return
            context = self.context()
            if context.pop('quiet', False) and record.levelno < logging.ERROR: return
            trace = ''
            if record.exc_info and record.exc_info[0]:
                frames = traceback.extract_tb(record.exc_info[2])[-12:]
                trace = record.exc_info[0].__name__ + ': ' + str(record.exc_info[1]) + '\n' + '\n'.join(f'{Path(frame.filename).name}:{frame.lineno} in {frame.name}' for frame in frames)
            self.journal().record(record.levelname, record.name, record.getMessage(), trace, **context)
        except Exception:
            pass
