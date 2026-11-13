"""Bounded, coalesced background preparation with foreground priority.

This pool owns only background cache work. HTTP streaming keeps its existing
budget. Keys and callables are discarded on completion; diagnostics are aggregate.

A job runs in a pool thread, which does not inherit the submitting request's
playback identity, so the identity is captured at submit and re-bound while the
action runs. Prepare work therefore resolves the caller's own stream worker and
reads the caller's own playback generation.
"""
from collections import deque
from concurrent.futures import Future
import threading
import time

from player_state import IDENTITY


class AudioJobs:
    def __init__(self, workers=4, capacity=16, prefetch_workers=2, prefetch_capacity=2):
        if not 0 < prefetch_workers < workers or capacity < workers or prefetch_capacity < 1:
            raise ValueError('Invalid audio worker limits')
        self.workers, self.capacity = workers, capacity
        self.prefetch_workers, self.prefetch_capacity = prefetch_workers, prefetch_capacity
        self.condition = threading.Condition()
        self.pending = deque()
        self.jobs = {}
        self.active = self.prefetch_active = 0
        self.started = self.closed = False
        self.counts = dict(submitted=0, coalesced=0, promoted=0, rejected=0,
                           completed=0, failed=0, total_ms=0, max_ms=0)

    def submit(self, key, action, prefetch=False):
        """Return a shared Future, or None on saturation; never wait for capacity.

        A requested play promotes a queued speculative job and replaces its
        generation-sensitive callable. Running jobs remain shared.
        """
        identity = IDENTITY.get()
        with self.condition:
            if self.closed:
                return None
            old = self.jobs.get(key)
            if old is not None:
                self.counts['coalesced'] += 1
                if old['prefetch'] and not prefetch and not old['running']:
                    old.update(prefetch=False, action=action)
                    self.counts['promoted'] += 1
                    self.condition.notify_all()
                return old['future']
            speculative = sum(job['prefetch'] for job in self.jobs.values())
            if len(self.jobs) >= self.capacity or (prefetch and speculative >= self.prefetch_capacity):
                self.counts['rejected'] += 1
                return None
            job = dict(key=key, action=action, prefetch=prefetch, running=False,
                       identity=identity, future=Future())
            self.jobs[key] = job
            self.pending.append(job)
            self.counts['submitted'] += 1
            if not self.started:
                self.started = True
                for index in range(self.workers):
                    threading.Thread(target=self._run, daemon=True,
                                     name=f'audio-prepare-{index}').start()
            self.condition.notify_all()
            return job['future']

    def contains(self, key):
        with self.condition:
            return key in self.jobs

    def snapshot(self):
        with self.condition:
            return dict(self.counts, active=self.active, queued=len(self.pending),
                        prefetch_active=self.prefetch_active, capacity=self.capacity,
                        workers=self.workers)

    def close(self):
        """Reject new work, cancel queued work, let running subprocesses finish."""
        with self.condition:
            self.closed = True
            cancelled = list(self.pending)
            self.pending.clear()
            for job in cancelled:
                del self.jobs[job['key']]
            self.condition.notify_all()
        # Future callbacks may submit work, so never call them under our lock.
        for job in cancelled:
            job['future'].cancel()

    def _take(self):
        for job in self.pending:
            if not job['prefetch']:
                break
        else:
            job = self.pending[0] if self.pending and self.prefetch_active < self.prefetch_workers else None
        if job is not None:
            self.pending.remove(job)
            job['running'] = True
            self.active += 1
            self.prefetch_active += int(job['prefetch'])
        return job

    def _run(self):
        while True:
            with self.condition:
                job = self._take()
                while job is None:
                    if self.closed:
                        return
                    self.condition.wait()
                    job = self._take()
            started = time.monotonic()
            result, error = None, None
            token = IDENTITY.set(job['identity'])
            try:
                if job['future'].set_running_or_notify_cancel():
                    result = job['action']()
            except BaseException as exc:
                error = exc
            finally:
                elapsed = int((time.monotonic() - started) * 1000)
                with self.condition:
                    del self.jobs[job['key']]
                    self.active -= 1
                    self.prefetch_active -= int(job['prefetch'])
                    self.counts['completed'] += 1
                    self.counts['failed'] += int(error is not None or not result)
                    self.counts['total_ms'] += elapsed
                    self.counts['max_ms'] = max(self.counts['max_ms'], elapsed)
                    self.condition.notify_all()
                IDENTITY.reset(token)
            if not job['future'].cancelled():
                if error is None:
                    job['future'].set_result(result)
                else:
                    job['future'].set_exception(error)
