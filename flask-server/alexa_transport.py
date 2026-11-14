"""Account-local, bounded Amazon transport with per-device command coalescing."""
import asyncio
import time
from dataclasses import dataclass, field


class TransportDeferred(Exception):
    """Temporary transport pressure, never an expired Amazon login."""


@dataclass
class _Lane:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    revision: int = 0
    latest_key: object = None
    latest_task: object = None
    waiters: dict = field(default_factory=dict)


class AmazonTransport:
    def __init__(self, timeout=8.0, cooldown=15.0, clock=time.monotonic):
        self.timeout, self.cooldown, self.clock = timeout, cooldown, clock
        self.cooldown_until = 0.0
        self.lanes = {}

    async def run(self, serial, key, operation):
        lane = self.lanes.setdefault(serial, _Lane())
        task = lane.latest_task
        if task is None or task.done() or lane.latest_key != key:
            lane.revision += 1
            revision = lane.revision
            async def execute():
                async with lane.lock:
                    if revision != lane.revision:
                        raise TransportDeferred('Playback request was replaced by a newer command.')
                    remaining = self.cooldown_until - self.clock()
                    if remaining > 0:
                        raise TransportDeferred(f'Amazon is temporarily busy. Retry in {max(1, int(remaining + 1))} seconds.')
                    try:
                        return await asyncio.wait_for(operation(), timeout=self.timeout)
                    except asyncio.TimeoutError as error:
                        self.cooldown_until = max(self.cooldown_until, self.clock() + self.cooldown)
                        raise TransportDeferred('Amazon did not respond in time. Wait a few seconds before retrying.') from error
                    except Exception as error:
                        status = getattr(error, 'status', None) or getattr(getattr(error, 'response', None), 'status_code', None)
                        if status == 429 or 'TooManyRequests' in type(error).__name__ or 'too many requests' in str(error).lower():
                            self.cooldown_until = max(self.cooldown_until, self.clock() + self.cooldown)
                            raise TransportDeferred('Amazon is rate-limiting commands. Wait a few seconds before retrying.') from error
                        raise
            task = asyncio.create_task(execute())
            lane.latest_key, lane.latest_task = key, task
        lane.waiters[task] = lane.waiters.get(task, 0) + 1
        try:
            return await asyncio.shield(task)
        finally:
            lane.waiters[task] -= 1
            if not lane.waiters[task]:
                del lane.waiters[task]
                if not task.done():
                    task.cancel()
                    # Cancellation must finish before the next command gets the lane.
                    try:
                        await task
                    except (asyncio.CancelledError, Exception):
                        pass


async def bounded_transport(owner, serial, key, operation):
    gate = getattr(owner, '_amazon_transport', None)
    if gate is None:
        gate = owner._amazon_transport = AmazonTransport()
    return await gate.run(serial, key, operation)
