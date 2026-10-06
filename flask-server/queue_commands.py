"""Bounded command deduplication. Caller serializes check/apply/record with the queue lock."""
from collections import OrderedDict
import hashlib
import json
import time

class QueueCommands:
    def __init__(self, clock=time.monotonic, capacity=256, ttl=600):
        self.clock, self.capacity, self.ttl = clock, capacity, ttl
        self.commands = OrderedDict()
    def replay(self, command_id, body):
        now = self.clock()
        while self.commands and next(iter(self.commands.values()))[0] <= now:
            self.commands.popitem(last=False)
        digest = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
        old = self.commands.get(command_id)
        if old and old[1] != digest:
            raise ValueError('Command id reused for a different queue mutation')
        return (dict(old[2]) if old else None), digest
    def record(self, command_id, digest, response):
        self.commands[command_id] = (self.clock() + self.ttl, digest, dict(response))
        while len(self.commands) > self.capacity:
            self.commands.popitem(last=False)
