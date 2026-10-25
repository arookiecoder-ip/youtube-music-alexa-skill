"""Per-identity playback state (Phase 2.1).

Proves that the dict/lock proxies isolate identities without changing the call
sites, and that an unbound caller still resolves to the owner.
"""
import threading
import unittest

from player_state import (IDENTITY, IdentityLock, PlayerStateProxy, PlayerStateRegistry,
                          PlayerStateView)


def _factory(identity):
    return {'queue': [], 'video_id': '', 'playing': False, 'identity': identity}


class PlayerStateTests(unittest.TestCase):
    def setUp(self):
        self.registry = PlayerStateRegistry(_factory)
        self.state = PlayerStateView(self.registry)
        IDENTITY.set('owner')

    def tearDown(self):
        IDENTITY.set('owner')

    def test_unbound_identity_is_the_owner(self):
        self.assertEqual(self.registry.key(), 'owner')
        self.state['video_id'] = 'owner-track'
        self.assertEqual(self.registry.state_for('owner')['video_id'], 'owner-track')

    def test_two_identities_do_not_share_a_queue(self):
        IDENTITY.set('user:a')
        self.state['queue'].append({'video_id': 'a1'})
        self.state['video_id'] = 'a1'
        IDENTITY.set('user:b')
        self.assertEqual(self.state['queue'], [])
        self.state['queue'].append({'video_id': 'b1'})
        self.state['video_id'] = 'b1'
        IDENTITY.set('user:a')
        self.assertEqual([i['video_id'] for i in self.state['queue']], ['a1'])
        IDENTITY.set('owner')
        self.assertEqual(self.state['queue'], [])

    def test_view_behaves_like_a_dict(self):
        IDENTITY.set('user:a')
        self.state.update({'video_id': 'x', 'playing': True})
        self.assertIn('video_id', self.state)
        self.assertEqual(self.state.get('missing', 'fallback'), 'fallback')
        self.assertEqual(self.state.setdefault('volume', 5), 5)
        self.assertEqual(sorted(self.state.keys()),
                         ['identity', 'playing', 'queue', 'video_id', 'volume'])
        self.assertEqual(len(self.state), 5)
        snapshot = self.state.copy()
        snapshot['queue'].append('leaked')
        self.assertEqual(self.state['queue'], [])
        self.assertEqual(self.state.pop('volume'), 5)
        del self.state['playing']
        self.assertNotIn('playing', self.state)

    def test_locks_are_per_identity_and_reentrant(self):
        lock = IdentityLock(self.registry)
        IDENTITY.set('user:a')
        with lock:
            with lock:
                self.assertTrue(self.registry.lock_for('user:a')._is_owned())
            # A different identity's lock is free while this one is held.
            acquired = []
            thread = threading.Thread(
                target=lambda: acquired.append(self.registry.lock_for('user:b').acquire(blocking=False))
            )
            thread.start()
            thread.join()
            self.assertEqual(acquired, [True])

    def test_the_factory_builds_the_identity_it_was_given(self):
        self.assertEqual(self.registry.state_for('user:z')['identity'], 'user:z')
        IDENTITY.set('user:z')
        self.assertEqual(self.registry.state()['identity'], 'user:z')
        self.assertEqual(self.registry.state_for('owner')['identity'], 'owner')

    def test_explicit_state_for_does_not_depend_on_the_context(self):
        IDENTITY.set('user:a')
        self.registry.state_for('owner')['video_id'] = 'from-owner'
        self.assertEqual(self.registry.state_for('user:a')['video_id'], '')
        self.assertEqual(self.registry.known_keys(), ['owner', 'user:a'])


class Lease:
    """Stand-in for PlaybackOutput: reachable only through attribute access."""

    def __init__(self):
        self.serial = ''

    def snapshot(self):
        return {'serial': self.serial}


class PlayerStateProxyTests(unittest.TestCase):
    def setUp(self):
        IDENTITY.set('owner')
        self.outputs = PlayerStateRegistry(lambda identity: Lease())
        self.lease = PlayerStateProxy(self.outputs)

    def tearDown(self):
        IDENTITY.set('owner')

    def test_each_identity_gets_its_own_object(self):
        self.lease.serial = 'owner-echo'
        IDENTITY.set('user:a')
        self.assertEqual(self.lease.serial, '')
        self.lease.serial = 'a-phone'
        IDENTITY.set('owner')
        self.assertEqual(self.lease.serial, 'owner-echo')
        self.assertEqual(self.lease.snapshot(), {'serial': 'owner-echo'})

    def test_attribute_deletion_reaches_the_current_object(self):
        # mock.patch.object() restores a patched method by deleting it from the
        # instance, so a proxy that only forwarded reads and writes would leak
        # the patch onto the owner's object after the test finished.
        IDENTITY.set('user:a')
        del self.lease.serial
        self.assertFalse(hasattr(self.outputs.state_for('user:a'), 'serial'))
        self.assertTrue(hasattr(self.outputs.state_for('owner'), 'serial'))


if __name__ == '__main__':
    unittest.main()
