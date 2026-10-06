import sys
from pathlib import Path
import threading
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from playback_output import PlaybackOutput, OutputConflict


class PlaybackOutputTests(unittest.TestCase):
    def test_phone_claim_pauses_echo_before_permission_to_play(self):
        pause = Mock()
        output = PlaybackOutput()
        state = output.phone('mobile', pause, 'echo-one')
        pause.assert_called_once()
        self.assertEqual(state['playback_output'], 'phone')
        self.assertTrue(output.owns_phone('mobile', state['output_token']))
        output.phone('mobile', pause)
        pause.assert_called_once()  # Editing this phone's queue must not dispatch again.

    def test_failed_echo_pause_never_grants_phone_ownership(self):
        output = PlaybackOutput()
        before = output.snapshot()
        with self.assertRaises(RuntimeError):
            output.phone('mobile', Mock(side_effect=RuntimeError('Echo unreachable')))
        self.assertEqual(output.snapshot()['output_token'], before['output_token'])
        self.assertEqual(output.snapshot()['playback_output'], 'alexa')

    def test_web_play_cannot_dispatch_before_phone_pause_ack(self):
        output = PlaybackOutput()
        phone = output.phone('mobile', lambda: None)
        changed = threading.Event()
        dispatched = threading.Event()
        errors = []
        def web_play():
            try:
                output.alexa('echo-one', on_change=changed.set)
                dispatched.set()
            except Exception as error:
                errors.append(error)
        worker = threading.Thread(target=web_play)
        worker.start()
        self.assertTrue(changed.wait(1))
        self.assertFalse(dispatched.is_set())
        remote = output.snapshot()
        self.assertFalse(output.owns_phone('mobile', phone['output_token']))
        output.acknowledge('mobile', remote['output_token'])
        worker.join(1)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertTrue(dispatched.is_set())

    def test_unresponsive_phone_blocks_echo_instead_of_parallel_playback(self):
        output = PlaybackOutput()
        output.phone('mobile', lambda: None)
        with self.assertRaises(OutputConflict):
            output.alexa(timeout=0.01)

    def test_closed_process_expired_lease_does_not_block_later_alexa(self):
        now = [10.0]
        output = PlaybackOutput(clock=lambda: now[0])
        old = output.phone('mobile', lambda: None)
        now[0] += 13
        latest = output.alexa('echo-one')
        self.assertEqual(latest['playback_output'], 'alexa')
        self.assertNotEqual(old['output_token'], latest['output_token'])
        self.assertFalse(output.owns_phone('mobile', old['output_token']))

    def test_late_phone_heartbeat_and_ack_cannot_reclaim_alexa(self):
        output = PlaybackOutput()
        old = output.phone('mobile', lambda: None)
        latest = output.alexa(wait=False)
        with self.assertRaises(OutputConflict):
            output.heartbeat('mobile', old['output_token'])
        with self.assertRaises(OutputConflict):
            output.acknowledge('mobile', old['output_token'])
        self.assertEqual(output.snapshot()['output_token'], latest['output_token'])

    def test_new_phone_intent_supersedes_a_waiting_web_handoff(self):
        output = PlaybackOutput()
        output.phone('mobile', lambda: None)
        old_handoff = output.alexa(wait=False)
        newest = output.phone('mobile', lambda: None)
        with self.assertRaises(OutputConflict):
            output.wait_released(old_handoff['output_token'], timeout=0.01)
        self.assertTrue(output.owns_phone('mobile', newest['output_token']))

    def test_second_phone_cannot_play_while_first_lease_is_active(self):
        output = PlaybackOutput()
        first = output.phone('first', lambda: None)
        with self.assertRaises(OutputConflict):
            output.phone('second', lambda: None)
        self.assertTrue(output.owns_phone('first', first['output_token']))

    def test_new_phone_play_rejects_old_same_phone_cursor_reports(self):
        output = PlaybackOutput()
        pause = Mock()
        old = output.phone('mobile', pause)
        current = output.phone('mobile', pause)
        self.assertNotEqual(old['output_token'], current['output_token'])
        self.assertFalse(output.owns_phone('mobile', old['output_token']))
        self.assertTrue(output.owns_phone('mobile', current['output_token']))
        with self.assertRaises(OutputConflict):
            output.heartbeat('mobile', old['output_token'])
        pause.assert_called_once()

    def test_guarded_app_handoff_cannot_steal_a_newer_phone_play(self):
        output = PlaybackOutput()
        old = output.phone('mobile', lambda: None)
        latest = output.phone('mobile', lambda: None)
        with self.assertRaises(OutputConflict):
            output.alexa(expected_owner='mobile', expected_token=old['output_token'])
        self.assertTrue(output.owns_phone('mobile', latest['output_token']))
