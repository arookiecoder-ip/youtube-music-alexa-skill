import sys
from pathlib import Path
import unittest
from unittest.mock import Mock
import threading
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mobile_devices import MobileDevices
from playback_output import PlaybackOutput, OutputConflict


class MobileDevicesTests(unittest.TestCase):
    def setUp(self):
        self.now = [100.0]
        self.registry = MobileDevices(clock=lambda: self.now[0])
        self.registry.online('one', 'session', 'Phone', [])

    def test_expired_device_disappears_and_cannot_receive_commands(self):
        self.now[0] += 16
        self.assertEqual(self.registry.list(), [])
        with self.assertRaises(ValueError):
            self.registry.command('one', 'token', 'play', {})

    def test_old_process_cannot_remove_new_process(self):
        self.registry.online('one', 'new-session', 'Phone', [])
        self.assertFalse(self.registry.offline('one', 'session'))
        self.assertEqual(len(self.registry.list()), 1)
        self.assertTrue(self.registry.offline('one', 'new-session'))

    def test_old_heartbeat_cannot_replace_new_session(self):
        self.registry.online('one', 'new-session', 'Phone', [])
        with self.assertRaises(ValueError):
            self.registry.online('one', 'session', 'Old Phone', [])
        self.assertEqual(self.registry.list()[0]['name'], 'Phone')

    def test_command_survives_lost_response_until_ack_and_duplicate_id_is_idempotent(self):
        self.registry.command('one', 'token', 'next', {}, 'command-1')
        self.registry.command('one', 'token', 'next', {}, 'command-1')
        first = self.registry.online('one', 'session', 'Phone', [])
        second = self.registry.online('one', 'session', 'Phone', [])
        self.assertEqual(first, second)
        self.assertEqual(len(first), 1)
        self.assertEqual(self.registry.online('one', 'session', 'Phone', ['command-1']), [])

    def test_wait_response_does_not_extend_dead_phone_presence(self):
        original_deadline = self.registry.devices['one']['until']
        self.now[0] += 8
        self.assertEqual(self.registry.pending_commands('one', 'session'), [])
        self.assertEqual(self.registry.devices['one']['until'], original_deadline)
        self.registry.offline('one', 'session')
        with self.assertRaises(ValueError):
            self.registry.pending_commands('one', 'session')

    def test_commands_expire(self):
        self.registry.command('one', 'token', 'play', {})
        self.now[0] += 10
        self.registry.online('one', 'session', 'Phone', [])
        self.now[0] += 10
        self.registry.online('one', 'session', 'Phone', [])
        self.now[0] += 11
        self.assertEqual(self.registry.online('one', 'session', 'Phone', []), [])

    def test_phone_transfer_waits_for_pause_and_fences_old_writes(self):
        output = PlaybackOutput()
        first = output.phone('one', lambda: None)
        changed = threading.Event()
        completed = threading.Event()
        result = []
        def transfer():
            result.append(output.transfer_phone('two', first['output_token'], Mock(), on_change=changed.set))
            completed.set()
        worker = threading.Thread(target=transfer)
        worker.start()
        self.assertTrue(changed.wait(1))
        pending = output.snapshot()
        self.assertFalse(completed.is_set())
        self.assertTrue(pending['handoff_pending'])
        self.assertFalse(output.owns_phone('one', first['output_token']))
        self.assertFalse(output.owns_phone('two', pending['output_token']))
        with self.assertRaises(OutputConflict):
            output.heartbeat('two', pending['output_token'])
        output.acknowledge('one', pending['output_token'])
        worker.join(1)
        self.assertFalse(worker.is_alive())
        self.assertTrue(output.owns_phone('two', result[0]['output_token']))

    def test_closed_source_releases_pending_transfer(self):
        output = PlaybackOutput()
        first = output.phone('one', lambda: None)
        with self.assertRaises(OutputConflict):
            output.transfer_phone('two', first['output_token'], lambda: None, timeout=0.01)
        output.close_phone('one')
        pending = output.snapshot()
        self.assertFalse(pending['handoff_pending'])
        self.assertTrue(output.owns_phone('two', pending['output_token']))

    def test_stale_transfer_cannot_steal_new_playback(self):
        output = PlaybackOutput()
        first = output.phone('one', lambda: None)
        second = output.phone('one', lambda: None)
        with self.assertRaises(OutputConflict):
            output.transfer_phone('two', first['output_token'], lambda: None)
        self.assertTrue(output.owns_phone('one', second['output_token']))

    def test_alexa_to_remote_phone_pauses_echo_before_grant(self):
        output = PlaybackOutput()
        pause = Mock(side_effect=RuntimeError('offline'))
        before = output.snapshot()
        with self.assertRaises(RuntimeError):
            output.transfer_phone('two', before['output_token'], pause)
        self.assertEqual(output.snapshot(), before)

    def test_closing_current_phone_invalidates_lease_without_claiming_alexa(self):
        output = PlaybackOutput()
        state = output.phone('one', lambda: None)
        output.close_phone('one')
        self.assertFalse(output.owns_phone('one', state['output_token']))
        self.assertEqual(output.snapshot()['playback_output'], 'phone')
        self.assertEqual(output.snapshot()['output_owner'], '')

    def test_target_cannot_bypass_source_pause_with_an_explicit_claim(self):
        output = PlaybackOutput()
        source = output.phone('one', lambda: None)
        with self.assertRaises(OutputConflict):
            output.transfer_phone('two', source['output_token'], lambda: None, timeout=0.01)
        with self.assertRaises(OutputConflict):
            output.phone('two', lambda: None)
        pending = output.snapshot()
        output.acknowledge('one', pending['output_token'])
        target = output.phone('two', lambda: None)
        with self.assertRaises(OutputConflict):
            output.acknowledge('one', target['output_token'])
        self.assertTrue(output.owns_phone('two', target['output_token']))

    def test_switch_to_alexa_during_phone_transfer_still_waits_for_actual_source(self):
        output = PlaybackOutput()
        source = output.phone('one', lambda: None)
        with self.assertRaises(OutputConflict):
            output.transfer_phone('two', source['output_token'], lambda: None, timeout=0.01)
        latest = output.alexa(wait=False)
        output.acknowledge('one', latest['output_token'])
        output.wait_released(latest['output_token'], timeout=0.01)
        self.assertEqual(output.snapshot()['playback_output'], 'alexa')

class PresenceWakeTests(unittest.TestCase):
    def test_long_wait_is_bounded_below_device_expiry(self):
        registry = MobileDevices()
        registry.changed.wait_for = Mock()
        registry.wait(registry.revision, 25)
        self.assertEqual(registry.changed.wait_for.call_args.kwargs['timeout'], 8)
        self.assertLess(8, registry.ttl)
        registry.wait(registry.revision, -1)
        self.assertEqual(registry.changed.wait_for.call_args.kwargs['timeout'], 0)

    def test_explicit_close_wakes_a_long_request_and_cannot_resurrect_session(self):
        registry = MobileDevices()
        registry.online('target', 'session', 'Phone', [])
        revision = registry.revision
        done = threading.Event()
        worker = threading.Thread(target=lambda: (registry.wait(revision, 8), done.set()))
        worker.start()
        registry.offline('target', 'session')
        self.assertTrue(done.wait(.5))
        worker.join(1)
        with self.assertRaises(ValueError):
            registry.online('target', 'session', 'Phone', [])

    def test_waiting_request_wakes_on_command_without_a_poll_delay(self):
        registry = MobileDevices()
        registry.online('target', 'session', 'Phone', [])
        revision = registry.revision
        ready, finished = threading.Event(), threading.Event()
        def wait():
            ready.set()
            registry.wait(revision, 2)
            finished.set()
        worker = threading.Thread(target=wait)
        worker.start()
        self.assertTrue(ready.wait(1))
        registry.command('target', 'token', 'play', {})
        self.assertTrue(finished.wait(.5))
        worker.join(1)

    def test_volume_update_wakes_controllers_but_unchanged_heartbeat_does_not(self):
        registry = MobileDevices()
        registry.online('target', 'session', 'Phone', [], volume=20)
        revision = registry.revision
        registry.online('target', 'session', 'Phone', [], volume=20)
        self.assertEqual(registry.revision, revision)
        registry.online('target', 'session', 'Phone', [], volume=72)
        self.assertGreater(registry.revision, revision)
        self.assertEqual(registry.list()[0]['volume'], 72)

    def test_invalid_volume_cannot_create_a_broken_presence_record(self):
        registry = MobileDevices()
        with self.assertRaises(ValueError):
            registry.online('target', 'session', 'Phone', [], volume=-1)
        self.assertEqual(registry.list(), [])

    def test_delayed_fallback_cannot_replace_a_newer_explicit_play(self):
        now = [100.0]
        output = PlaybackOutput(clock=lambda: now[0])
        old = output.phone('two', lambda: None)
        now[0] = 113
        new = output.phone('one', lambda: None)
        self.assertFalse(output.fallback_phone(old['output_token'], 'controller', source_closed=True))
        self.assertTrue(output.owns_phone('one', new['output_token']))
        self.assertGreater(new['output_revision'], old['output_revision'])

    def test_ack_resets_target_lease_so_dead_targets_do_not_hold_a_double_lease(self):
        output = PlaybackOutput()
        source = output.phone('one', lambda: None)
        with self.assertRaises(OutputConflict):
            output.transfer_phone('two', source['output_token'], lambda: None, timeout=.01)
        pending = output.snapshot()
        output.acknowledge('one', pending['output_token'])
        self.assertLessEqual(output.snapshot()['phone_lease_ms'], 12000)
        self.assertGreater(output.snapshot()['phone_lease_ms'], 11900)
        self.assertGreater(output.snapshot()['output_revision'], pending['output_revision'])

    def test_actual_volume_steps_are_reported_and_retained(self):
        self.registry = MobileDevices()
        self.registry.online('one', 'session', 'Phone', [], 66, 15)
        self.assertEqual(self.registry.list()[0]['volume_steps'], 15)
        self.registry.online('one', 'session', 'Phone', [])
        self.assertEqual(self.registry.list()[0]['volume_steps'], 15)

    def test_invalid_volume_steps_do_not_replace_presence(self):
        self.registry = MobileDevices()
        self.registry.online('one', 'session', 'Phone', [])
        for invalid in (0, -1, True, '15', 1001):
            with self.assertRaises(ValueError):
                self.registry.online('one', 'session', 'Phone', [], 50, invalid)
        self.assertIsNone(self.registry.list()[0]['volume_steps'])
