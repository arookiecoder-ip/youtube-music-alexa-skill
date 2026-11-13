"""Adversarial account, capability and resource boundaries using temporary state."""
import gc
import os
import tempfile
import threading
import time
import weakref
from pathlib import Path
from unittest import mock

import sys
sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1])]
from test_selfservice import SelfServiceTestBase, PASSWORD
from multiuser.accounts import AccountError
from multiuser.security import totp_at
from youtube_stream import YouTubeStream, Resolver, StreamError
from stream_pools import _YouTubeResolverPool
from playback_store import PlaybackStore, PlaybackPersistence
import provider_commands


class AccountBoundaries(SelfServiceTestBase):
    def setUp(self):
        super().setUp()
        self.user = self.accounts.get_user_by_username('owner')
        self.password = 'Owner!Passw0rd'

    def enable_factor(self):
        secret = self.accounts.begin_totp_enrollment(self.user, current_password=self.password)['secret']
        self.accounts.confirm_totp_enrollment(self.user, code=totp_at(secret, int(time.time()) // 30))
        self.user = self.accounts.get_user(self.user['id'])
        self.accounts.db.execute('UPDATE users SET totp_last_step=0 WHERE id=?', (self.user['id'],))
        return secret

    def test_password_change_preserves_existing_access_version(self):
        version = self.accounts.security_version(self.user['id'])
        self.accounts.change_own_password(self.user, self.password, 'Changed!Passw0rd')
        self.assertEqual(self.accounts.security_version(self.user['id']), version)

    def test_old_mailbox_proof_is_required_by_service_and_alternate_route(self):
        with self.assertRaises(AccountError):
            self.accounts.update_own_profile(self.user, email='other@example.test', current_password=self.password)
        self.sign_in(self.user)
        response = self.client.post('/api/account/profile', json={'email':'other@example.test', 'current_password':self.password}, headers=self.json_headers())
        self.assertNotEqual(response.status_code, 200)
        self.assertEqual(self.accounts.get_user(self.user['id'])['email'], self.user['email'])

    def test_email_is_staged_until_new_mailbox_verification(self):
        code = self.accounts.create_email_code(self.user['id'], 'reauth')
        updated = self.accounts.update_own_profile(self.user, email='other@example.test', current_password=self.password, email_code=code)
        self.assertEqual(updated['email'], self.user['email'])
        self.assertEqual(updated['pending_email'], 'other@example.test')
        code = self.accounts.create_email_code(self.user['id'], 'verify')
        self.assertTrue(self.accounts.consume_email_code(self.user['id'], 'verify', code))
        self.assertEqual(self.accounts.verify_email_with_token(self.user['id'])['email'], 'other@example.test')

    def test_totp_counter_cannot_be_replayed_in_next_window(self):
        secret = self.enable_factor()
        fixed = 1800000000
        with mock.patch('time.time', return_value=fixed):
            self.assertTrue(self.accounts.verify_totp_code(self.user['id'], totp_at(secret, fixed//30)))
        with mock.patch('time.time', return_value=fixed+30):
            self.assertFalse(self.accounts.verify_totp_code(self.user['id'], totp_at(secret, fixed//30)))

    def test_correct_password_does_not_clear_second_factor_lock(self):
        secret = self.enable_factor()
        for _ in range(self.layer.config.login_max_attempts):
            self.accounts.verify_totp_code(self.user['id'], 'not-a-code')
        self.accounts.authenticate(self.user['username'], self.password)
        self.assertFalse(self.accounts.verify_totp_code(self.user['id'], totp_at(secret, int(time.time())//30)))

    def test_expired_pending_factor_cannot_be_enabled(self):
        secret = self.accounts.begin_totp_enrollment(self.user, current_password=self.password)['secret']
        self.accounts.db.execute('UPDATE users SET totp_pending_at=? WHERE id=?', (time.time()-99999, self.user['id']))
        with self.assertRaises(AccountError):
            self.accounts.confirm_totp_enrollment(self.user, code=totp_at(secret, int(time.time())//30))
        self.assertFalse(self.accounts.get_user(self.user['id'])['has_totp'])

    def test_password_alone_cannot_remove_or_replace_factor(self):
        self.enable_factor()
        with self.assertRaises(AccountError):
            self.accounts.disable_totp(self.user, current_password=self.password)
        with self.assertRaises(AccountError):
            self.accounts.begin_totp_enrollment(self.user, current_password=self.password, allow_existing=True, verified=True)

    def test_magic_get_does_not_consume_or_authenticate(self):
        self.enable_factor()
        token = self.accounts.create_email_token(self.user['id'], 'magic')
        self.assertEqual(self.client.get('/admin/magic/'+token).status_code, 200)
        self.assertTrue(self.accounts.peek_email_token(token, 'magic'))
        with self.client.session_transaction() as state:
            csrf = state['mu_csrf']
            self.assertNotIn('mu_sid', state)
        response = self.client.post('/admin/magic/'+token, data={'_csrf':csrf})
        self.assertIn(b'name="pending"', response.data)
        with self.client.session_transaction() as state:
            self.assertNotIn('mu_sid', state)

    def test_recovery_does_not_store_account_identity_in_cookie(self):
        for identifier in (self.user['username'], 'nonexistent-identity'):
            client = self.app.test_client();client.get('/forgot-password')
            with client.session_transaction() as state:csrf=state['mu_ss_csrf']
            client.post('/forgot-password', data={'_csrf':csrf,'identifier':identifier})
            with client.session_transaction() as state:self.assertNotIn('mu_ss_pending_verify',state)


class ResourceBoundaries(__import__('unittest').TestCase):
    def test_retired_relay_frees_buffer_and_callback_reference(self):
        stream = YouTubeStream(lambda:None, resolver=object())
        stream.current.activate('synthetic')
        stream.current.file = tempfile.TemporaryFile()
        file = stream.current.file
        stream.close(); self.assertTrue(file.closed)
        ref = weakref.ref(stream); del stream; gc.collect()
        self.assertIsNone(ref())

    def test_cookie_files_are_private_unique_and_removed_after_execution(self):
        with tempfile.TemporaryDirectory() as root:
            source=Path(root)/'original';source.write_text('synthetic')
            a=[];b=[];provider_commands.attach_cookies(a,source);provider_commands.attach_cookies(b,source)
            self.assertNotEqual(a[-1],b[-1]);self.assertEqual(os.stat(a[-1]).st_mode & 0o777,0o600)
            with mock.patch('provider_commands.subprocess.run', side_effect=TimeoutError):
                with self.assertRaises(TimeoutError):provider_commands.run(a)
            self.assertFalse(Path(a[-1]).exists());provider_commands.cleanup(b)

    def test_busy_resolver_is_not_evicted(self):
        pool=_YouTubeResolverPool(limit=1);first=pool.get('/synthetic/a')
        first._pending[1]=object()
        with self.assertRaises(StreamError):pool.get('/synthetic/b')
        self.assertIs(pool.get('/synthetic/a'),first)
        first._pending.clear();first.retire_if_idle()

    def test_negative_tombstone_checks_are_cached(self):
        with tempfile.TemporaryDirectory() as root:
            store=PlaybackStore(str(Path(root)/'state.db'));store.dead('user:synthetic')
            with mock.patch.object(store,'_connection',side_effect=AssertionError('synchronous DB')):
                self.assertFalse(store.dead('user:synthetic'))

    def test_global_stream_admission_is_shared_across_accounts(self):
        slots=threading.BoundedSemaphore(1)
        first=YouTubeStream(lambda:None,resolver=object(),global_slots=slots)
        second=YouTubeStream(lambda:None,resolver=object(),global_slots=slots)
        self.addCleanup(first.close);self.addCleanup(second.close)
        slots.acquire()
        try:
            with self.assertRaises(StreamError):first.open('dQw4w9WgXcQ')
            with self.assertRaises(StreamError):second.open('dQw4w9WgXcQ')
        finally:slots.release()
