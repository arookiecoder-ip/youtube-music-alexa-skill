"""Per-identity Amazon/Alexa sessions (Phase 2.3)."""
import asyncio
import os
import tempfile
import time
import unittest
from unittest import mock

import alexa_remote
from player_state import IDENTITY


class CookieDirTests(unittest.TestCase):
    def test_the_owner_keeps_the_directory_this_install_always_used(self):
        with mock.patch.object(alexa_remote, 'ALEXA_COOKIE_DIR', '/data/alexa'):
            self.assertEqual(alexa_remote.cookie_dir_for('owner'), '/data/alexa')
            self.assertEqual(alexa_remote.cookie_dir_for(''), '/data/alexa')

    def test_each_account_gets_its_own_directory(self):
        with mock.patch.object(alexa_remote, 'ALEXA_COOKIE_DIR', '/data/alexa'):
            self.assertEqual(alexa_remote.cookie_dir_for('user:a'),
                             os.path.join('/data/alexa', 'accounts', 'user_a'))
            self.assertNotEqual(alexa_remote.cookie_dir_for('user:a'),
                                alexa_remote.cookie_dir_for('user:b'))

    def test_an_identity_cannot_escape_the_cookie_directory(self):
        with mock.patch.object(alexa_remote, 'ALEXA_COOKIE_DIR', '/data/alexa'):
            path = alexa_remote.cookie_dir_for('user:../../etc')
            self.assertTrue(path.startswith(os.path.join('/data/alexa', 'accounts') + os.sep))


class PerIdentitySessionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.registry = alexa_remote.PlayerStateRegistry(
            lambda identity: alexa_remote.AlexaRemote(
                cookie_dir=alexa_remote.cookie_dir_for(identity)))

    def tearDown(self):
        IDENTITY.set('owner')

    def test_two_accounts_get_separate_sessions_and_the_owner_stays_shared(self):
        first = self.registry.state_for('user:a')
        second = self.registry.state_for('user:b')
        self.assertIsInstance(first, alexa_remote.AlexaRemote)
        self.assertIsNot(first, second)
        self.assertNotEqual(first.cookie_dir, second.cookie_dir)
        self.assertIs(self.registry.state_for('user:a'), first)

    def test_the_session_resolves_from_the_callers_identity(self):
        remote = alexa_remote.PlayerStateProxy(self.registry)
        IDENTITY.set('owner')
        self.assertEqual(remote.cookie_dir, self.registry.state_for('owner').cookie_dir)
        IDENTITY.set('user:a')
        self.assertEqual(remote.cookie_dir, self.registry.state_for('user:a').cookie_dir)

    def test_the_installed_module_keeps_one_session_per_identity(self):
        owner_state = alexa_remote._remote_registry.state_for('owner')
        account_state = alexa_remote._remote_registry.state_for('user:a')
        self.addCleanup(lambda: alexa_remote._remote_registry._states.pop('user:a', None))
        self.assertIs(owner_state, alexa_remote._owner_remote)
        self.assertIsNot(account_state, owner_state)
        self.assertTrue(account_state.cookie_dir.startswith(
            os.path.join(alexa_remote.ALEXA_COOKIE_DIR, 'accounts')))


class PurgeTests(unittest.TestCase):
    def _tree(self, directory):
        """Two accounts' sessions under the owner's directory, as on disk."""
        owner_dir = directory
        account_dir = os.path.join(owner_dir, 'accounts', 'user_a')
        for path, files in ((owner_dir, ('alexa_media.owner.pickle',)),
                            (account_dir, ('alexa_media.them.pickle',))):
            os.makedirs(os.path.join(path, '.storage'), exist_ok=True)
            for name in files:
                open(os.path.join(path, name), 'w').close()
            open(os.path.join(path, '.storage', 'last_email.txt'), 'w').close()
        return owner_dir, account_dir

    def test_signing_out_clears_only_that_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            owner_dir, account_dir = self._tree(directory)
            alexa_remote.AlexaRemote(cookie_dir=owner_dir)._clear_persisted_session()
            self.assertEqual(os.listdir(owner_dir), ['accounts'])
            self.assertTrue(os.path.isfile(os.path.join(account_dir, 'alexa_media.them.pickle')))
            self.assertTrue(os.path.isfile(os.path.join(account_dir, '.storage', 'last_email.txt')))

            alexa_remote.AlexaRemote(cookie_dir=account_dir)._clear_persisted_session()
            self.assertEqual(os.listdir(account_dir), [])
            self.assertTrue(os.path.isdir(owner_dir))

    def test_signing_out_without_a_session_is_not_an_error(self):
        with tempfile.TemporaryDirectory() as directory:
            alexa_remote.AlexaRemote(
                cookie_dir=os.path.join(directory, 'never-created'))._clear_persisted_session()


class LoginProxyClaimTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(lambda: setattr(alexa_remote, '_proxy_holder', [None]))
        self.first, self.second = object(), object()

    def test_only_one_login_holds_the_port(self):
        self.assertTrue(alexa_remote._proxy_claim(self.first))
        self.assertFalse(alexa_remote._proxy_claim(self.second))
        self.assertTrue(alexa_remote._proxy_claim(self.first))
        alexa_remote._release_proxy_claim(self.first)
        self.assertTrue(alexa_remote._proxy_claim(self.second))

    def test_releasing_another_identitys_claim_does_nothing(self):
        alexa_remote._proxy_claim(self.first)
        alexa_remote._release_proxy_claim(self.second)
        self.assertFalse(alexa_remote._proxy_claim(self.second))

    def test_an_abandoned_login_stops_blocking_anyone(self):
        alexa_remote._proxy_claim(self.first)
        self.assertIsNone(alexa_remote._abandoned_proxy())
        alexa_remote._proxy_claim_expires[0] = time.monotonic() - 1
        self.assertIs(alexa_remote._abandoned_proxy(), self.first)

    def test_a_still_polling_login_keeps_its_claim(self):
        alexa_remote._proxy_claim(self.first)
        expires = time.monotonic() + alexa_remote.PROXY_CLAIM_TTL
        alexa_remote._proxy_claim_expires[0] = time.monotonic() - 1
        alexa_remote._renew_proxy_claim(self.first)
        self.assertGreaterEqual(alexa_remote._proxy_claim_expires[0], expires - 1)
        self.assertIsNone(alexa_remote._abandoned_proxy())

    def test_a_new_login_stops_an_abandoned_one_before_binding(self):
        with tempfile.TemporaryDirectory() as directory:
            stale = alexa_remote.AlexaRemote(cookie_dir=directory)
            taker = alexa_remote.AlexaRemote(cookie_dir=directory)
            alexa_remote._proxy_claim(stale)
            alexa_remote._proxy_claim_expires[0] = time.monotonic() - 1
            try:
                self.assertTrue(asyncio.run(taker._claim_proxy()))
            finally:
                if stale._loop:
                    stale._loop.call_soon_threadsafe(stale._loop.stop)
            self.assertIs(alexa_remote._proxy_holder[0], taker)
            self.assertFalse(stale._proxy_started)

    def test_a_live_login_cannot_be_stolen(self):
        with tempfile.TemporaryDirectory() as directory:
            holder = alexa_remote.AlexaRemote(cookie_dir=directory)
            taker = alexa_remote.AlexaRemote(cookie_dir=directory)
            alexa_remote._proxy_claim(holder)
            self.assertFalse(asyncio.run(taker._claim_proxy()))
            self.assertIs(alexa_remote._proxy_holder[0], holder)


if __name__ == '__main__':
    unittest.main()
