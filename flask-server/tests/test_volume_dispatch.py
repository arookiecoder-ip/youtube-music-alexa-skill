import threading
import time
import unittest

from volume_dispatch import VolumeDispatch


class VolumeDispatchTests(unittest.TestCase):
    def spawn(self, run):
        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        return thread

    def settled(self, gate, key=('alice', 'echo'), timeout=2):
        with gate.condition:
            self.assertTrue(gate.condition.wait_for(lambda: not gate.states[key]['pending'], timeout))
        return gate.status(key)

    def test_rapid_slider_changes_send_only_last_value(self):
        gate = VolumeDispatch(debounce=.03, interval=0, wait=0)
        sent, applied = [], []
        for value in (10, 20, 30, 80, 58):
            gate.submit(('alice','echo'), value, lambda v: sent.append(v), self.spawn, applied.append)
        self.settled(gate)
        self.assertEqual(sent, [58])
        self.assertEqual(applied, [58])

    def test_change_during_slow_request_does_not_queue_intermediate_values(self):
        gate = VolumeDispatch(debounce=0, interval=0, wait=0)
        started, release = threading.Event(), threading.Event()
        sent, applied = [], []
        def send(value):
            sent.append(value)
            if value == 10:
                started.set()
                self.assertTrue(release.wait(2))
        gate.submit(('alice','echo'), 10, send, self.spawn, applied.append)
        self.assertTrue(started.wait(1))
        for value in (30, 50, 70):
            gate.submit(('alice','echo'), value, send, self.spawn, applied.append)
        release.set()
        self.settled(gate)
        self.assertEqual(sent, [10,70])
        self.assertEqual(applied, [70])

    def test_rate_limit_keeps_latest_value_and_retries_automatically(self):
        gate = VolumeDispatch(debounce=0, interval=0, retry=.03, wait=0)
        sent, applied = [], []
        failed = threading.Event()
        def send(value):
            sent.append(value)
            if len(sent) == 1:
                failed.set()
                return 'Amazon is rate-limiting commands. Wait a few seconds before retrying.'
        gate.submit(('alice','echo'), 40, send, self.spawn, applied.append)
        self.assertTrue(failed.wait(1))
        gate.submit(('alice','echo'), 58, send, self.spawn, applied.append)
        self.settled(gate)
        self.assertEqual(sent, [40,58])
        self.assertEqual(applied, [58])

    def test_unavailable_account_does_not_block_another_account_or_device(self):
        gate = VolumeDispatch(debounce=0, interval=0, retry=.02, lifetime=.08, wait=0)
        gate.submit(('alice','echo'), 40, lambda v: 'Amazon is temporarily busy.', self.spawn, lambda v: None)
        applied = []
        gate.submit(('bob','echo'), 60, lambda v: None, self.spawn, applied.append)
        self.settled(gate, ('bob','echo'))
        self.assertEqual(applied, [60])
        self.assertIsNotNone(self.settled(gate)['error'])

    def test_device_switch_and_account_purge_discard_queued_changes(self):
        gate = VolumeDispatch(debounce=.03, interval=0, wait=0)
        sent = []
        gate.submit(('alice','echo'), 40, lambda v: sent.append(v), self.spawn, lambda v: None, valid=lambda:False)
        self.assertIsNotNone(self.settled(gate)['error'])
        self.assertEqual(sent, [])
        gate.submit(('alice','echo'), 60, lambda v: sent.append(v), self.spawn, lambda v: None)
        gate.cancel_identity('alice')
        time.sleep(.05)
        self.assertIsNone(gate.status(('alice','echo')))
        self.assertEqual(sent, [])

    def test_permanent_failure_does_not_publish_success(self):
        gate = VolumeDispatch(debounce=0, interval=0, wait=0)
        applied = []
        gate.submit(('alice','echo'), 50, lambda v: 'offline', self.spawn, applied.append)
        self.assertEqual(self.settled(gate)['error'], 'offline')
        self.assertEqual(applied, [])
