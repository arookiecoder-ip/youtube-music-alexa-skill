"""Deleting an account ends its artifacts, not just its row.

The purge is the part of account deletion that cannot be expressed in SQL: a queue
snapshot, an Amazon cookie directory, a YouTube credential file, a download jar, a
Chromium profile and a handful of in-process dicts keyed by the identity string.
These tests keep each of those honest, and keep the retry queue honest too --
because a purge that half-completed is a privacy failure, not a cosmetic one.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import account_purge
import alexa_remote
from playback_store import PlaybackPersistence, PlaybackStore
from player_state import PlayerStateRegistry
from youtube_browser_session import YouTubeBrowserSessionManager

USER_ID = "11111111-2222-3333-4444-555555555555"
IDENTITY = "user:" + USER_ID
OTHER_ID = "66666666-7777-8888-9999-000000000000"
OTHER_IDENTITY = "user:" + OTHER_ID

# One database for ``server.py`` itself, decided before it is imported: see
# ``ServerPurgeTests.setUpClass``.
_SERVER_TMP = tempfile.mkdtemp(prefix="purge-server-")
SERVER_DB = os.path.join(_SERVER_TMP, "server.db")


def tearDownModule():
    shutil.rmtree(_SERVER_TMP, ignore_errors=True)


class TempDirTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="purge-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class RemoveFileTests(TempDirTest):
    def test_a_file_is_removed_and_a_missing_one_is_nothing(self):
        path = os.path.join(self.tmp, "jar.txt")
        with open(path, "w") as handle:
            handle.write("cookies")
        self.assertTrue(account_purge.remove_file(path))
        self.assertFalse(os.path.exists(path))
        self.assertFalse(account_purge.remove_file(path))

    def test_an_empty_path_is_refused_rather_than_reaching_the_default(self):
        # The owner's own jar is what an empty identity resolves to; deleting it
        # would be the bug this guard exists to catch.
        self.assertFalse(account_purge.remove_file(""))

    def test_a_directory_is_never_treated_as_a_derived_file(self):
        with self.assertRaises(ValueError):
            account_purge.remove_file(self.tmp)

    def test_a_symlink_is_removed_as_a_link_or_not_at_all(self):
        target = os.path.join(self.tmp, "target.txt")
        link = os.path.join(self.tmp, "link.txt")
        with open(target, "w") as handle:
            handle.write("someone else's")
        os.symlink(target, link)
        with self.assertRaises(ValueError):
            account_purge.remove_file(link)
        self.assertTrue(os.path.isfile(target))


class PendingPurgesTests(TempDirTest):
    def setUp(self):
        super().setUp()
        self.db_path = os.path.join(self.tmp, "data.db")
        self.queue = account_purge.PendingPurges(
            lambda: sqlite3.connect(self.db_path))

    def test_a_queued_purge_is_not_due_immediately(self):
        self.queue.record(USER_ID, "sidecar busy")
        self.assertEqual(self.queue.waiting(), 1)
        self.assertEqual(self.queue.due(), [])

    def test_backoff_grows_and_one_row_per_account_is_enough(self):
        first = self.queue.record(USER_ID, "a")
        self.assertTrue(first)
        self.queue.record(USER_ID, "b")
        self.assertEqual(self.queue.waiting(), 1)
        conn = sqlite3.connect(self.db_path)
        try:
            attempts, error = conn.execute(
                'SELECT attempts, last_error FROM pending_purges WHERE user_id=?',
                (USER_ID,)).fetchone()
        finally:
            conn.close()
        self.assertEqual(attempts, 2, "the second failure folded into the first row")
        self.assertEqual(error, "b")

    def test_a_due_purge_runs_and_is_then_forgotten(self):
        self.queue.record(USER_ID)
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute('UPDATE pending_purges SET next_at = ?', (time.time() - 1,))
            conn.commit()
        finally:
            conn.close()
        self.assertEqual(self.queue.due(), [USER_ID])
        self.queue.done(USER_ID)
        self.assertEqual(self.queue.waiting(), 0)

    def test_a_purge_that_never_succeeds_is_abandoned_with_an_error(self):
        self.queue.record(USER_ID)
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute('UPDATE pending_purges SET created_at = ?',
                         (time.time() - account_purge.RETRY_LIMIT_SECONDS - 1,))
            conn.commit()
        finally:
            conn.close()
        with self.assertLogs("account_purge", level="ERROR") as captured:
            self.assertFalse(self.queue.record(USER_ID, "still busy"))
        self.assertEqual(self.queue.waiting(), 0)
        self.assertIn("giving up", captured.output[0])

    def test_an_empty_account_id_never_enters_the_queue(self):
        self.assertFalse(self.queue.record("", "nothing to retry"))
        self.assertEqual(self.queue.waiting(), 0)


class PlaybackTombstoneTests(TempDirTest):
    def setUp(self):
        super().setUp()
        self.path = os.path.join(self.tmp, "data.db")
        self.store = PlaybackStore(self.path)

    def rows(self):
        conn = sqlite3.connect(self.path)
        try:
            return {row[0] for row in conn.execute(
                'SELECT identity FROM playback_snapshot')}
        finally:
            conn.close()

    def save(self, identity, title="Song"):
        self.store.save(identity, {'video_id': 'vid', 'title': title, 'queue': []},
                        {'playback_output': 'alexa', 'output_serial': 'S'}, 1000)

    def test_a_tombstone_erases_the_snapshot_it_replaces(self):
        self.save(IDENTITY)
        self.assertIn(IDENTITY, self.rows())
        self.store.tombstone(IDENTITY)
        self.assertNotIn(IDENTITY, self.rows())

    def test_writing_after_a_tombstone_writes_nothing(self):
        # The case that matters: a heartbeat thread still holding this identity's
        # state when the account was deleted.
        self.store.tombstone(IDENTITY)
        self.save(IDENTITY)
        self.assertEqual(self.rows(), set())
        self.assertEqual(self.store.load(IDENTITY), {})

    def test_one_account_is_not_tombstone_for_everyone(self):
        self.store.tombstone(IDENTITY)
        self.save(OTHER_IDENTITY)
        self.save("owner")
        self.assertEqual(self.rows(), {OTHER_IDENTITY, "owner"})

    def test_a_tombstone_survives_a_restart(self):
        self.store.tombstone(IDENTITY)
        revived = PlaybackStore(self.path)
        self.assertTrue(revived.dead(IDENTITY))
        revived.save(IDENTITY, {'video_id': 'v', 'queue': []}, {}, 0)
        self.assertEqual(self.rows(), set())

    def test_an_expired_tombstone_is_forgotten_from_the_table(self):
        self.store.tombstone(IDENTITY, seconds=60)
        conn = sqlite3.connect(self.path)
        try:
            conn.execute('UPDATE playback_dead SET expires_at = ? WHERE identity=?',
                         (time.time() - 1, IDENTITY))
            conn.commit()
        finally:
            conn.close()
        # A new store, so the in-process cache cannot be what answers.
        revived = PlaybackStore(self.path)
        self.assertFalse(revived.dead(IDENTITY))
        conn = sqlite3.connect(self.path)
        try:
            self.assertEqual(conn.execute(
                'SELECT COUNT(*) FROM playback_dead').fetchone()[0], 0)
        finally:
            conn.close()

    def test_a_tombstone_never_shortens_one_already_running(self):
        self.store.tombstone(IDENTITY, seconds=600)
        self.store.tombstone(IDENTITY, seconds=1)
        self.assertTrue(self.store.dead(IDENTITY))
        conn = sqlite3.connect(self.path)
        try:
            deadline = conn.execute(
                'SELECT expires_at FROM playback_dead WHERE identity=?', (IDENTITY,)).fetchone()[0]
        finally:
            conn.close()
        self.assertGreater(deadline, time.time() + 500)

    def test_the_persistence_worker_refuses_a_deleted_identity(self):
        persist = PlaybackPersistence(self.store, lambda: None)
        try:
            self.store.tombstone(IDENTITY)
            persist.submit({'video_id': 'v', 'queue': []}, {'playback_output': 'alexa'},
                           0, identity=IDENTITY)
            self.assertNotIn(IDENTITY, persist.pending)
            time.sleep(0.2)
            self.assertEqual(self.rows(), set())
            # An identity that is alive is still written.
            persist.submit({'video_id': 'w', 'queue': []}, {}, 0, identity=OTHER_IDENTITY)
            deadline = time.time() + 2
            while OTHER_IDENTITY not in self.rows() and time.time() < deadline:
                time.sleep(0.05)
            self.assertIn(OTHER_IDENTITY, self.rows())
        finally:
            persist.wake.set()


class AlexaCookieDirTests(TempDirTest):
    def setUp(self):
        super().setUp()
        self.root = os.path.join(self.tmp, "alexa_cookies")
        os.makedirs(os.path.join(self.root, "accounts"))
        patch = mock.patch.object(alexa_remote, "ALEXA_COOKIE_DIR", self.root)
        patch.start()
        self.addCleanup(patch.stop)

    def make(self, identity):
        path = alexa_remote.cookie_dir_for(identity)
        os.makedirs(path, exist_ok=True)
        with open(os.path.join(path, "cookies.pkl"), "w") as handle:
            handle.write("session")
        return path

    def test_the_owner_directory_is_never_purgeable(self):
        # Empty is the owner's too: it is the directory every account lives under.
        for identity in ("", "owner", None):
            with self.assertRaises(ValueError):
                alexa_remote.purge_cookie_dir(identity)
        self.assertTrue(os.path.isdir(self.root))

    def test_an_account_directory_is_removed_and_nothing_else_with_it(self):
        other = self.make("user:" + OTHER_ID)
        mine = self.make(IDENTITY)
        with open(os.path.join(self.root, "cookies.pkl"), "w") as handle:
            handle.write("owner session")
        self.assertTrue(alexa_remote.purge_cookie_dir(IDENTITY))
        self.assertFalse(os.path.exists(mine))
        self.assertTrue(os.path.isdir(other))
        self.assertTrue(os.path.isfile(os.path.join(self.root, "cookies.pkl")))

    def test_a_missing_account_directory_is_nothing_to_do(self):
        self.assertFalse(alexa_remote.purge_cookie_dir(IDENTITY))

    def test_a_symlinked_account_directory_is_refused(self):
        outside = os.path.join(self.tmp, "somewhere_else")
        os.makedirs(outside)
        path = alexa_remote.cookie_dir_for(IDENTITY)
        os.symlink(outside, path)
        with self.assertRaises(ValueError):
            alexa_remote.purge_cookie_dir(IDENTITY)
        self.assertTrue(os.path.isdir(outside))


class RegistryDiscardTests(unittest.TestCase):
    def test_discarding_drops_the_state_and_the_lock_together(self):
        registry = PlayerStateRegistry(factory=lambda identity: {'identity': identity})
        lock = registry.lock_for(IDENTITY)
        registry.state_for(IDENTITY)['marker'] = True
        self.assertTrue(registry.discard(IDENTITY))
        self.assertIsNone(registry.peek(IDENTITY))
        self.assertFalse(registry.discard(IDENTITY), "second time there was nothing to drop")
        # A rebound identity starts from the factory, not from the deleted account.
        self.assertEqual(registry.state_for(IDENTITY), {'identity': IDENTITY})
        self.assertIsNot(registry.lock_for(IDENTITY), lock)

    def test_peek_never_resurrects_an_entry(self):
        registry = PlayerStateRegistry(factory=lambda identity: {})
        self.assertIsNone(registry.peek("nobody"))
        self.assertEqual(registry.known_keys(), [])


class _FakeController:
    def __init__(self):
        self.calls = []

    def call(self, path, method="GET", payload=None):
        self.calls.append((path, method, payload))
        return {}


class BrowserSessionForgetTests(unittest.TestCase):
    def setUp(self):
        self.controller = _FakeController()
        self.manager = YouTubeBrowserSessionManager(self.controller, lambda *a, **k: None)

    def test_forget_drops_the_entry_without_closing_the_browser(self):
        self.manager.start(IDENTITY, profile_key=USER_ID)
        self.controller.calls.clear()
        self.assertTrue(self.manager.forget(IDENTITY))
        self.assertNotIn(IDENTITY, self.manager._auth)
        self.assertEqual([call[0] for call in self.controller.calls], [],
                         "forget must not fire the network close that revoke does")
        self.assertFalse(self.manager.forget(IDENTITY))

    def test_forget_does_not_create_the_entry_it_is_clearing(self):
        self.manager.forget("never-seen")
        self.assertNotIn("never-seen", self.manager._auth)

    def test_forgetting_the_busy_identity_hands_the_browser_back(self):
        self.manager.start(IDENTITY, profile_key=USER_ID)
        self.assertEqual(self.manager._busy, IDENTITY)
        self.manager.forget(IDENTITY)
        self.assertIsNone(self.manager._busy)

    def test_another_identity_still_holding_a_lease_keeps_the_browser_busy(self):
        self.manager.start(IDENTITY, profile_key=USER_ID)
        second, _ = self.manager.start("user:" + OTHER_ID, profile_key=OTHER_ID)
        self.assertIsNone(second, "one Chromium cannot serve two logins at once")
        self.manager.forget(IDENTITY)
        # Nothing holds the browser now, so the next start is not blocked by the
        # account that was just deleted.
        self.assertIsNone(self.manager._busy)
        token, _ = self.manager.start("user:" + OTHER_ID, profile_key=OTHER_ID)
        self.assertIsNotNone(token)


class ServerPurgeTests(TempDirTest):
    """The purge itself, against ``server.py``'s real collaborators."""

    @classmethod
    def setUpClass(cls):
        from tests.test_session_cookie_config import _install_stubs

        _install_stubs()
        # ``server.py`` reads DB_FILE at import and builds its store and background
        # loops from it there, so importing it unguarded inside a running container
        # would point the status watchdog and the purge sweeper at the live install's
        # data -- one of those threads then issues real yt-dlp calls that land in
        # whichever test happens to be patching ``subprocess.run``. The per-test
        # patches below still repoint the module; this only keeps the import harmless.
        with mock.patch.dict(os.environ, {"DB_FILE": SERVER_DB}):
            import server

        cls.server = server
        # Import starts the sweep loop whenever the multi-user layer is enabled. A
        # second pair of hands on the same queue would race these assertions, and
        # the tests drive one pass of _account_purge_sweep() directly anyway.
        server._account_purge_stop.set()
        # ``_install_stubs`` keeps Amazon's SDK out of server's import by replacing
        # the whole module -- which also strips the cookie-directory helpers a purge
        # needs. The real module is already imported at the top of this file, so the
        # purge under test gets its actual collaborator back.
        cls._collaborator = mock.patch.object(server, "alexa_remote", alexa_remote)
        cls._collaborator.start()

    @classmethod
    def tearDownClass(cls):
        cls._collaborator.stop()

    def setUp(self):
        super().setUp()
        server = self.server
        self.db_path = os.path.join(self.tmp, "server.db")
        self.auth_dir = os.path.join(self.tmp, "youtube_auth")
        self.cookie_dir = os.path.join(self.tmp, "alexa_cookies", "accounts")
        os.makedirs(self.auth_dir)
        os.makedirs(self.cookie_dir)
        jar_dir = os.path.join(self.tmp, "jars")
        os.makedirs(jar_dir)
        patches = [
            mock.patch.object(server, "DB_FILE", self.db_path),
            mock.patch.object(server, "_db_initialized", False),
            # The store took its path at import, so patching DB_FILE alone would
            # write tombstones into the install's real snapshot file.
            mock.patch.object(server, "_playback_store", PlaybackStore(self.db_path)),
            mock.patch.object(server._youtube_credentials, "account_dir", self.auth_dir),
            mock.patch.object(server.download_cookies, "saved_path",
                              lambda: Path(jar_dir, "download_cookies.txt")),
            mock.patch.object(alexa_remote, "ALEXA_COOKIE_DIR",
                              os.path.join(self.tmp, "alexa_cookies")),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        server._ensure_db()
        self.sidecar = mock.Mock()
        self.sidecar.call.return_value = {"deleted": True}
        patch = mock.patch.object(server, "_youtube_browser_sessions",
                                  YouTubeBrowserSessionManager(self.sidecar, lambda *a, **k: None))
        patch.start()
        self.addCleanup(patch.stop)

    def arm_state(self):
        """Give the identity something in every per-identity holder."""
        server = self.server
        server._player_registry.state_for(IDENTITY)['video_id'] = 'v'
        server._playback_output_registry.state_for(IDENTITY).serial = 'S'
        server._volume_registry.state_for(IDENTITY)['serial-1'] = 40
        server._alexa_volume_grace_registry.state_for(IDENTITY)['serial-1'] = time.time()
        server._alexa_queue_decisions_registry.state_for(IDENTITY)['token'] = {'a': 1}
        timer = mock.Mock()
        server._alexa_queue_sync_slot_registry.state_for(IDENTITY)['timer'] = timer
        server._playback_failover_registry.state_for(IDENTITY).limit = 5
        server.alexa_remote._remote_registry.state_for(IDENTITY)
        return timer

    def registries(self):
        server = self.server
        return (server._player_registry, server._playback_output_registry,
                server._volume_registry, server._alexa_volume_grace_registry,
                server._alexa_queue_decisions_registry, server._alexa_queue_sync_slot_registry,
                server._playback_failover_registry, server.alexa_remote._remote_registry)

    def test_an_identity_that_is_not_an_account_is_refused(self):
        # The helper that maps the bootstrap owner's row id onto 'owner' is exactly
        # what must not be used here: a purge of 'owner' would take the human's own
        # queue, cookies and profile with it.
        server = self.server
        server._playback_store.save("owner", {'video_id': 'mine', 'queue': []}, {}, 0)
        server._player_registry.state_for("owner")['video_id'] = 'mine'
        for not_an_account in ("", "owner", None, "not-a-uuid"):
            self.assertEqual(server._purge_account_artifacts(not_an_account), ["identity"])
        self.assertEqual(server._playback_store._dead, {},
                         "a refusal must not tombstone anything")
        self.assertEqual(server._player_registry.peek("owner")['video_id'], 'mine')
        conn = sqlite3.connect(self.db_path)
        try:
            self.assertEqual(conn.execute(
                'SELECT COUNT(*) FROM playback_snapshot WHERE identity="owner"').fetchone()[0], 1)
        finally:
            conn.close()
        self.assertEqual(self.sidecar.call.call_count, 0,
                         "a refused purge must not reach for anyone's browser profile")

    def test_the_purge_drops_every_in_process_holder(self):
        server = self.server
        timer = self.arm_state()
        self.assertEqual(server._purge_account_artifacts(USER_ID), [])
        for registry in self.registries():
            self.assertIsNone(registry.peek(IDENTITY))
        timer.cancel.assert_called_once_with()

    def test_the_owner_state_survives_an_account_purge(self):
        server = self.server
        server._player_registry.state_for("owner")['video_id'] = 'mine'
        server._volume_registry.state_for("owner")['serial-1'] = 11
        self.arm_state()
        server._purge_account_artifacts(USER_ID)
        self.assertEqual(server._player_registry.state_for("owner")['video_id'], 'mine')
        self.assertEqual(server._volume_registry.state_for("owner")['serial-1'], 11)

    def test_the_files_and_the_snapshot_row_go_with_the_identity(self):
        server = self.server
        auth_path = os.path.join(self.auth_dir, USER_ID + ".json")
        jar_path = os.path.join(self.tmp, "jars", f"download_cookies_{USER_ID}.txt")
        with open(auth_path, "w") as handle:
            handle.write("{}")
        with open(jar_path, "w") as handle:
            handle.write("#netscape")
        account_dir = os.path.join(self.cookie_dir, IDENTITY.replace(":", "_"))
        os.makedirs(account_dir)
        server._playback_store.save(IDENTITY, {'video_id': 'v', 'queue': []}, {}, 0)
        self.assertEqual(server._purge_account_artifacts(USER_ID), [])
        for path in (auth_path, jar_path):
            self.assertFalse(os.path.exists(path), path)
        self.assertFalse(os.path.isdir(account_dir))
        conn = sqlite3.connect(self.db_path)
        try:
            self.assertEqual(conn.execute(
                'SELECT COUNT(*) FROM playback_snapshot WHERE identity=?',
                (IDENTITY,)).fetchone()[0], 0)
        finally:
            conn.close()
        self.assertTrue(server._playback_store.dead(IDENTITY))

    def test_the_owner_files_are_left_alone(self):
        server = self.server
        owner_auth = os.path.join(self.tmp, "headers_auth.json")
        owner_jar = os.path.join(self.tmp, "jars", "download_cookies.txt")
        with open(owner_auth, "w") as handle:
            handle.write("{}")
        with open(owner_jar, "w") as handle:
            handle.write("#netscape")
        server._purge_account_artifacts(USER_ID)
        self.assertTrue(os.path.isfile(owner_auth))
        self.assertTrue(os.path.isfile(owner_jar))

    def test_a_jam_token_is_closed_with_the_account(self):
        server = self.server
        with server.get_db() as conn:
            conn.execute('INSERT INTO jam_tokens (token, created_at, user_id) VALUES (?, ?, ?)',
                         ("jam-code", time.time(), USER_ID))
            conn.execute('INSERT INTO jam_tokens (token, created_at, user_id) VALUES (?, ?, ?)',
                         ("someone-elses", time.time(), OTHER_ID))
            conn.commit()
        server._jam_cache().clear()
        self.assertEqual(server._purge_account_artifacts(USER_ID), [])
        with server.get_db() as conn:
            remaining = [row[0] for row in conn.execute(
                'SELECT token FROM jam_tokens ORDER BY token')]
        self.assertEqual(remaining, ["someone-elses"])

    def test_a_sidecar_that_is_busy_is_reported_and_retried(self):
        server = self.server
        self.sidecar.call.return_value = {"error": "browser_in_use"}
        with mock.patch.object(server, "_pending_purges",
                              account_purge.PendingPurges(lambda: sqlite3.connect(self.db_path))):
            failures = server._purge_account_artifacts(USER_ID)
        self.assertTrue(any("browser profile" in item for item in failures))
        self.sidecar.call.assert_called_once_with(
            '/profile/delete', 'POST', {'profile': USER_ID})

    def make_purges_due(self):
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute('UPDATE pending_purges SET next_at = ?', (time.time() - 1,))
            conn.commit()
        finally:
            conn.close()

    def test_the_sweeper_finishes_what_a_delete_could_not(self):
        server = self.server
        queue = account_purge.PendingPurges(lambda: sqlite3.connect(self.db_path))
        with mock.patch.object(server, "_pending_purges", queue):
            self.sidecar.call.return_value = {"error": "browser_in_use"}
            server._purge_account_artifacts_hook(USER_ID)
            self.assertEqual(queue.waiting(), 1)
            self.make_purges_due()
            self.assertEqual(queue.due(), [USER_ID])

            # Still busy: the sweep re-queues rather than dropping the account.
            self.assertEqual(server._account_purge_sweep(), [])
            self.assertEqual(queue.waiting(), 1)

            self.make_purges_due()
            self.sidecar.call.return_value = {"deleted": True}
            self.assertEqual(server._account_purge_sweep(), [USER_ID])
            self.assertEqual(queue.waiting(), 0)


class SidecarProfileDeleteTests(TempDirTest):
    """The browser half of the purge, answered by the sidecar itself.

    The app treats any answer without a ``deleted`` flag as a failure worth
    retrying for a day, so a deletion that succeeds and then ends in a 500 is not
    cosmetic: it is a purge that never settles.
    """

    def setUp(self):
        super().setUp()
        import importlib.util

        self.profile_root = Path(self.tmp, "profile")
        self.profile_root.mkdir()
        source = Path(__file__).resolve().parents[2] / "browser-auth" / "service.py"
        spec = importlib.util.spec_from_file_location("purge_sidecar_test", source)
        self.sidecar = importlib.util.module_from_spec(spec)
        with mock.patch.dict(os.environ, {
                "YT_BROWSER_CONTROL_TOKEN": "control",
                "CHROME_USER_DATA_DIR": str(self.profile_root)}):
            spec.loader.exec_module(self.sidecar)
        self.client = self.sidecar.app.test_client()

    def delete(self, profile):
        return self.client.post("/profile/delete", json={"profile": profile},
                                headers={"X-Control-Token": "control"})

    def seed(self, key):
        directory = self.profile_root / key
        directory.mkdir(parents=True)
        (directory / "Preferences").write_text("{}")
        return directory

    def test_a_stored_profile_is_removed_and_answered_as_removed(self):
        gone = self.seed(USER_ID)
        kept = self.seed(OTHER_ID)
        self.sidecar._profile_key = str(gone)
        response = self.delete(USER_ID)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json().get("deleted"))
        self.assertFalse(gone.exists())
        self.assertTrue(kept.exists())
        # A sidecar still remembering the directory it deleted would try to drive
        # Chromium into a path that no longer exists.
        self.assertEqual(self.sidecar._profile_key, "")

    def test_a_profile_that_was_never_opened_answers_without_error(self):
        response = self.delete(USER_ID)
        self.assertEqual(response.status_code, 200)
        self.assertIs(response.get_json().get("deleted"), False)

    def test_the_owner_base_profile_is_untouchable(self):
        (self.profile_root / "Default").mkdir()
        self.assertEqual(self.delete("").status_code, 400)
        self.assertTrue((self.profile_root / "Default").exists())

    def test_a_key_that_is_not_one_path_segment_is_refused(self):
        for key in ("..", "../escape", "a/b", ".hidden", "x" * 65):
            with self.subTest(key=key):
                self.assertEqual(self.delete(key).status_code, 400)
        self.assertFalse((Path(self.tmp) / "escape").exists())


class AccountLayerHookTests(unittest.TestCase):
    def test_the_layer_calls_the_hook_with_the_id_after_the_row_is_gone(self):
        # The purge cannot look the account up, so the contract is one positional id.
        from multiuser.accounts import AccountService

        seen = []
        service = AccountService.__new__(AccountService)
        service._purge_hook = lambda user_id: seen.append(user_id)
        service._purge(USER_ID)
        self.assertEqual(seen, [USER_ID])

    def test_a_hook_that_raises_does_not_undo_the_deletion(self):
        from multiuser.accounts import AccountService

        service = AccountService.__new__(AccountService)

        def explode(user_id):
            raise RuntimeError("sidecar is down")

        service._purge_hook = explode
        with self.assertLogs("multiuser.accounts", level="ERROR"):
            service._purge(USER_ID)


if __name__ == "__main__":
    unittest.main()
