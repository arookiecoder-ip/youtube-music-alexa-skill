"""Latest-value Echo volume dispatch; intermediate slider values are disposable."""
import re
import threading
import time


class VolumeDispatch:
    def __init__(self, debounce=.18, interval=2.5, retry=15, lifetime=60, wait=.65, clock=time.monotonic):
        self.debounce, self.interval, self.retry = debounce, interval, retry
        self.lifetime, self.wait, self.clock = lifetime, wait, clock
        self.condition = threading.Condition()
        self.states = {}

    def status(self, key):
        with self.condition:
            state = self.states.get(key)
            return dict(state) if state else None

    def cancel_identity(self, identity):
        with self.condition:
            for key, state in list(self.states.items()):
                if key[0] == identity:
                    state['cancelled'] = True
                    self.states.pop(key, None)
            self.condition.notify_all()

    def submit(self, key, value, send, spawn, applied, valid=lambda: True):
        with self.condition:
            now = self.clock()
            for old, state in list(self.states.items()):
                if not state['pending'] and now - state['changed'] > 120:
                    self.states.pop(old, None)
            state = self.states.get(key)
            if state is None:
                state = dict(value=value, pending=False, error=None, changed=now,
                             sent_at=-float('inf'), cancelled=False, revision=0)
                self.states[key] = state
            state.update(value=value, changed=now, expires=now+self.lifetime, error=None)
            state['revision'] += 1
            revision = state['revision']
            if not state['pending']:
                state['pending'] = True
                spawn(lambda: self._run(key, state, send, applied, valid))
            self.condition.notify_all()
            self.condition.wait_for(lambda: not state['pending'] or state['revision'] != revision,
                                    timeout=self.wait)
            return dict(state)

    def _run(self, key, state, send, applied, valid):
        retry_at = 0
        while True:
            with self.condition:
                if state['cancelled'] or self.states.get(key) is not state:
                    return
                now = self.clock()
                if now >= state['expires']:
                    state.update(pending=False, error="Alexa couldn't apply the volume. Try again.")
                    self.condition.notify_all()
                    return
                ready = max(state['changed']+self.debounce, state['sent_at']+self.interval, retry_at)
                if now < ready:
                    self.condition.wait(timeout=min(ready-now, state['expires']-now))
                    continue
                value, revision = state['value'], state['revision']
            try:
                if not valid():
                    error = 'Playback device changed before the volume could be applied.'
                else:
                    error = send(value)
            except Exception:
                error = 'Alexa volume is temporarily unavailable. Try again.'
            with self.condition:
                if state['cancelled'] or self.states.get(key) is not state:
                    return
                state['sent_at'] = self.clock()
                if error:
                    transient = any(word in str(error).lower() for word in (
                        'temporarily', 'rate-limit', 'too many', 'did not respond', "didn't respond", 'timed out'))
                    if transient:
                        match = re.search(r'retry in (\d+) seconds', str(error), re.I)
                        retry_at = self.clock() + (int(match[1]) if match else self.retry)
                        continue
                    state.update(pending=False, error=str(error))
                    self.condition.notify_all()
                    return
                # Never publish an old acknowledgement over a newer slider value.
                if revision != state['revision']:
                    continue
            # State publication can acquire playback locks. Never hold this
            # condition while calling out: snapshots acquire those locks first.
            try:
                applied(value)
            except Exception:
                with self.condition:
                    state.update(pending=False, error='Could not refresh the applied volume. Try again.')
                    self.condition.notify_all()
                return
            with self.condition:
                if revision == state['revision']:
                    state.update(pending=False, error=None)
                    self.condition.notify_all()
                    return
