"""Serialize phone/Echo ownership independently of queue membership and buffering."""
import secrets
import threading
import time


class OutputConflict(Exception):
    pass


class PlaybackOutput:
    def __init__(self, clock=time.monotonic, lease_seconds=12):
        self.clock = clock
        self.lease_seconds = lease_seconds
        self.condition = threading.Condition()
        self.mode = 'alexa'
        self.owner = ''
        self.token = secrets.token_hex(16)
        self.serial = ''
        self.lease_until = 0
        self.pending_owner = ''
        self.pending_until = 0

    def snapshot(self):
        with self.condition:
            return {'playback_output': self.mode, 'output_owner': self.owner,
                    'output_token': self.token, 'output_serial': self.serial,
                    'phone_lease_ms': max(0, int((self.lease_until - self.clock()) * 1000))}

    def owns_phone(self, owner, token):
        with self.condition:
            return self.mode == 'phone' and bool(owner) and self.owner == owner and self.token == token

    def phone(self, owner, pause_echo, serial=''):
        with self.condition:
            if self.mode == 'phone' and self.owner == owner:
                # An explicit new phone play supersedes in-flight reports from its previous song.
                self.token = secrets.token_hex(16)
                if serial:
                    self.serial = serial
                self.lease_until = self.clock() + self.lease_seconds
                return self.snapshot()
            if self.mode == 'phone' and self.lease_until > self.clock():
                raise OutputConflict('Another phone owns playback. Stop it before switching devices.')
            before = self.token
            mode = self.mode
        if mode == 'alexa':
            pause_echo()  # Must succeed before the phone is allowed to start.
        with self.condition:
            if self.token != before:
                raise OutputConflict('Playback output changed during handoff. Retry.')
            self.mode, self.owner = 'phone', owner
            if serial:
                self.serial = serial
            self.token = secrets.token_hex(16)
            self.lease_until = self.clock() + self.lease_seconds
            self.pending_owner = ''
            self.condition.notify_all()
            return self.snapshot()

    def heartbeat(self, owner, token):
        with self.condition:
            if not self.owns_phone(owner, token):
                raise OutputConflict('Playback moved to another output.')
            self.lease_until = self.clock() + self.lease_seconds
            return self.snapshot()

    def alexa(self, serial='', on_change=lambda: None, wait=True, timeout=4,
              expected_owner=None, expected_token=None, source_paused=False):
        with self.condition:
            if expected_token is not None and not self.owns_phone(expected_owner, expected_token):
                raise OutputConflict('A newer phone play superseded this handoff.')
            if source_paused and expected_token is None:
                raise OutputConflict('A phone pause acknowledgement requires its current ownership token.')
            changed = self.mode != 'alexa'
            if changed:
                self.pending_owner = '' if source_paused else self.owner
                self.pending_until = self.lease_until
                self.mode, self.owner = 'alexa', ''
                self.token = secrets.token_hex(16)
            if serial:
                self.serial = serial
            token = self.token
        if changed:
            on_change()  # Notify outside the condition to avoid snapshot lock inversion.
        if wait:
            self.wait_released(token, timeout)
        return self.snapshot()

    def acknowledge(self, owner, token):
        with self.condition:
            if token != self.token or owner != self.pending_owner:
                raise OutputConflict('This handoff is no longer current.')
            self.pending_owner = ''
            self.condition.notify_all()
            return self.snapshot()

    def wait_released(self, token, timeout=4):
        deadline = self.clock() + timeout
        with self.condition:
            while self.pending_owner and self.pending_until > self.clock():
                if self.token != token:
                    raise OutputConflict('A newer output handoff replaced this request.')
                remaining = min(deadline, self.pending_until) - self.clock()
                if remaining <= 0:
                    raise OutputConflict('Waiting for the phone to pause. Retry playback.')
                self.condition.wait(remaining)
            if self.token != token:
                raise OutputConflict('A newer output handoff replaced this request.')
