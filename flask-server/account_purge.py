"""What a deleted account leaves behind, and the queue that finishes the job.

The ``users`` row is not where an account ends. Its queue snapshot, its Amazon
cookie directory, its Chromium profile, its YouTube auth file and its download
cookie jar all outlive it unless something removes them -- and a half-finished
purge is a privacy failure, not a cosmetic one.

So the steps that destroy secrets at rest are not trusted to one request's call
stack: they are retried from a small durable queue, because the sidecar can be
restarting, a directory can be held by a browser that is about to exit, and a
process can die between the row deletion and the last unlink.

Everything reachable from here is idempotent by construction. Re-running a purge
for an account that is already gone is a no-op, which is what lets the sweeper
treat "removed" and "was never there" as the same answer.
"""
from __future__ import annotations

import logging
import os
import time

logger = logging.getLogger(__name__)

# How long a deleted identity stays hidden from the playback store. Long enough
# that a heartbeat already in flight when the account died cannot land after it.
TOMBSTONE_SECONDS = 900

# A purge that is still failing after this long is an operator problem rather than
# a transient one, and the queue must not grow without bound.
RETRY_LIMIT_SECONDS = 86400

# Sweeper backoff: 30s, 2min, 8min, 32min, 2h, capped at two hours.
_BACKOFF_BASE = 30.0
_BACKOFF_CAP = 7200.0


def remove_file(path: str) -> bool:
    """Delete one file. False when there was nothing there to delete.

    The caller builds the path from an identity it has already validated, so this
    only refuses what is never a derived artifact: a directory, or a symlink whose
    target belongs to somebody else."""
    if not path:
        return False
    if os.path.islink(path) or os.path.isdir(path):
        raise ValueError(f"Refusing to remove {path}: not a regular file")
    try:
        os.unlink(path)
    except FileNotFoundError:
        return False
    return True


class PendingPurges:
    """One row per account whose artifacts are not gone yet.

    ``connect`` is whatever the host process uses to open its database, so this
    module never needs to know where the file lives or who created the connection."""

    def __init__(self, connect):
        self._connect = connect

    def _connection(self):
        conn = self._connect()
        conn.execute(
            'CREATE TABLE IF NOT EXISTS pending_purges ('
            ' user_id TEXT PRIMARY KEY,'
            ' attempts INTEGER NOT NULL DEFAULT 0,'
            ' last_error TEXT NOT NULL DEFAULT "",'
            ' created_at REAL NOT NULL,'
            ' next_at REAL NOT NULL)'
        )
        return conn

    def record(self, user_id: str, error: str = "") -> bool:
        """Queue a failed purge (or fold a new failure into the waiting one).

        Returns False once the retry window is exhausted -- the caller's signal to
        stop trying and say so in the log, because a queue cannot be allowed to
        grow just because one filesystem is unwritable."""
        if not user_id:
            return False
        now = time.time()
        conn = self._connection()
        try:
            row = conn.execute(
                'SELECT attempts, created_at FROM pending_purges WHERE user_id=?',
                (user_id,)).fetchone()
            attempts = (int(row[0]) if row else 0) + 1
            created = float(row[1]) if row else now
            if created and now - created > RETRY_LIMIT_SECONDS:
                conn.execute('DELETE FROM pending_purges WHERE user_id=?', (user_id,))
                conn.commit()
                logger.error(
                    "Artifacts of account %s are still present after %.0f hours; giving up. "
                    "Last error: %s", user_id, RETRY_LIMIT_SECONDS / 3600.0, error)
                return False
            delay = min(_BACKOFF_CAP, _BACKOFF_BASE * (4 ** min(attempts - 1, 4)))
            conn.execute(
                'INSERT OR REPLACE INTO pending_purges VALUES (?, ?, ?, ?, ?)',
                (user_id, attempts, (error or '')[:400], created, now + delay))
            conn.commit()
            return True
        finally:
            conn.close()

    def due(self, limit: int = 20) -> list[str]:
        conn = self._connection()
        try:
            return [row[0] for row in conn.execute(
                'SELECT user_id FROM pending_purges WHERE next_at <= ? '
                'ORDER BY next_at LIMIT ?', (time.time(), int(limit)))]
        finally:
            conn.close()

    def done(self, user_id: str) -> None:
        conn = self._connection()
        try:
            conn.execute('DELETE FROM pending_purges WHERE user_id=?', (user_id,))
            conn.commit()
        finally:
            conn.close()

    def waiting(self) -> int:
        conn = self._connection()
        try:
            return int(conn.execute('SELECT COUNT(*) FROM pending_purges').fetchone()[0] or 0)
        finally:
            conn.close()
