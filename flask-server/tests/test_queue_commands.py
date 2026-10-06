import unittest
from queue_commands import QueueCommands
class QueueCommandsTests(unittest.TestCase):
    def test_retry_is_idempotent_and_id_reuse_is_rejected(self):
        commands = QueueCommands()
        body = {'action': 'next', 'tracks': ['a']}
        result, digest = commands.replay('one', body)
        self.assertIsNone(result)
        commands.record('one', digest, {'ok': True, 'queue_version': 3})
        self.assertEqual(commands.replay('one', body)[0]['queue_version'], 3)
        with self.assertRaises(ValueError): commands.replay('one', {'action': 'next', 'tracks': ['b']})
    def test_expiry_and_capacity_bound_memory(self):
        now = [0]
        commands = QueueCommands(clock=lambda: now[0], capacity=2, ttl=5)
        for key in ('a', 'b', 'c'):
            _, digest = commands.replay(key, {'action': key})
            commands.record(key, digest, {'ok': True})
        self.assertIsNone(commands.replay('a', {'action': 'a'})[0])
        now[0] = 6
        self.assertIsNone(commands.replay('c', {'action': 'c'})[0])
