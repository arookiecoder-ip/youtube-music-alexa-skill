"""Permanent deletion and optional password changes for administrator-created accounts."""
from pathlib import Path
import re
import sys
from unittest import mock
sys.path[:0] = [str(Path(__file__).parents[1]), str(Path(__file__).parent)]
from test_multiuser import MultiUserTestBase
from multiuser.accounts import AccountError


class AccountLifecycleTests(MultiUserTestBase):
    def setUp(self):
        super().setUp()
        self.accounts = self.layer.accounts
        self.owner = self.accounts.get_user_by_username('owner')
        self.user = self.accounts.create_user(self.owner, 'delete-target', 'Target!Password12',
            email='delete-target@example.test', email_verified=True)
        self.cleanup = mock.Mock()
        self.accounts.set_purge_hook(self.cleanup)

    def deleted(self):
        self.accounts.delete_user(self.owner, self.user['id'])
        self.cleanup.reset_mock()

    def test_permanent_delete_cascades_credentials_and_cannot_be_restored(self):
        self.accounts.create_session(self.user, ip='127.0.0.1', user_agent='fixture')
        self.layer.alexa_access.set_enabled(self.user['id'], True)
        self.layer.alexa_access.rotate(self.user['id'])
        identity = self.layer.alexa_identity
        self.assertTrue(identity.pair(identity.issue(self.user), 'subject', 'device'))
        self.deleted()
        self.accounts.permanently_delete_user(self.owner, self.user['id'], self.user['username'])
        self.assertFalse(self.accounts.get_user(self.user['id'], include_deleted=True))
        for table in ('user_sessions','user_provider_credentials','user_preferences','user_email_tokens',
                      'alexa_access_tokens','alexa_bindings','alexa_pair_codes'):
            self.assertEqual(self.layer.db.scalar('SELECT count(*) FROM '+table+' WHERE user_id=?', (self.user['id'],)), 0, table)
        self.cleanup.assert_called_once_with(self.user['id'])
        with self.assertRaises(AccountError): self.accounts.restore_user(self.owner, self.user['id'])
        self.assertEqual(self.layer.db.scalar("SELECT count(*) FROM audit_log WHERE action='user_permanently_deleted'"), 1)

    def test_active_account_wrong_name_self_and_stale_owner_claims_are_rejected(self):
        with self.assertRaises(AccountError):
            self.accounts.permanently_delete_user(self.owner, self.user['id'], self.user['username'])
        self.deleted()
        for name in ('', 'DELETE', 'another-user'):
            with self.assertRaises(AccountError): self.accounts.permanently_delete_user(self.owner, self.user['id'], name)
        admin = self.accounts.create_user(self.owner, 'limited-admin', 'Admin!Password12', role='admin')
        for actor in (None, admin, {'id': admin['id'], 'role': 'owner', 'status': 'active'}):
            with self.assertRaises(AccountError): self.accounts.permanently_delete_user(actor, self.user['id'], self.user['username'])
        with self.assertRaises(AccountError): self.accounts.permanently_delete_user(self.owner, self.owner['id'], 'owner')
        self.assertIsNotNone(self.accounts.get_user(self.user['id'], include_deleted=True))
        self.cleanup.assert_not_called()

    def test_restored_account_cannot_be_erased_by_a_stale_deleted_page(self):
        self.deleted()
        self.accounts.restore_user(self.owner, self.user['id'])
        with self.assertRaises(AccountError): self.accounts.permanently_delete_user(self.owner, self.user['id'], self.user['username'])
        self.assertEqual(self.accounts.get_user(self.user['id'])['status'], 'disabled')

    def test_permanent_delete_route_requires_csrf_and_removes_it_from_deleted_list(self):
        self.deleted()
        self.login()
        path='/admin/users/'+self.user['id']+'/permanent-delete/'
        self.assertEqual(self.client.post(path,data={'confirmation':self.user['username']}).status_code,400)
        self.assertIsNotNone(self.accounts.get_user(self.user['id'],include_deleted=True))
        response=self.client.post(path,data={'_csrf':self.csrf(),'confirmation':self.user['username']})
        self.assertEqual(response.status_code,302)
        self.assertIn('status=deleted',response.location)
        self.assertNotIn(b'delete-target',self.client.get('/admin/users/?status=deleted').data)
        self.assertEqual(self.client.get('/admin/users/'+self.user['id']+'/').status_code,302)

    def test_other_accounts_and_the_pinned_owner_identity_survive_permanent_delete(self):
        other=self.accounts.create_user(self.owner,'second-owner','Another!Password12',role='owner')
        self.assertEqual(self.accounts.legacy_owner_id(),self.owner['id'])
        self.accounts.delete_user(other,self.owner['id'])
        self.accounts.permanently_delete_user(other,self.owner['id'],'owner')
        self.assertIsNotNone(self.accounts.get_user(other['id']))
        self.assertEqual(self.accounts.legacy_owner_id(),self.owner['id'])
        self.assertIsNotNone(self.accounts.get_user(self.user['id']))

    def test_default_password_is_ready_to_use_and_option_remains_explicit(self):
        self.assertFalse(self.user['must_change_password'])
        signed,error=self.accounts.authenticate('delete-target','Target!Password12')
        self.assertEqual(error,'')
        self.assertFalse(signed['must_change_password'])
        self.login()
        html=self.client.get('/admin/users/new/').data.decode()
        checkbox=re.search(r'<input[^>]+id="must_change_password"[^>]*>',html).group()
        self.assertNotIn('checked',checkbox)
        for name,extra,expected in [('normal-created',{},False),('temporary-created',{'must_change_password':'on'},True)]:
            response=self.client.post('/admin/users/new/',data={'_csrf':self.csrf(),'username':name,'role':'user','password':'Created!Password12',**extra})
            self.assertEqual(response.status_code,302)
            self.assertEqual(bool(self.accounts.get_user_by_username(name)['must_change_password']),expected)

    def test_generated_password_result_does_not_claim_a_forced_change(self):
        self.login()
        response=self.client.post('/admin/users/new/',data={'_csrf':self.csrf(),'username':'generated-user','role':'user','password':''})
        self.assertEqual(response.status_code,200)
        self.assertIn(b'A password change is not required',response.data)
        self.assertFalse(self.accounts.get_user_by_username('generated-user')['must_change_password'])
