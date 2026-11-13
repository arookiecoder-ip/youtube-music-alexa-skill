"""Server gates for guided security forms and retirement of delegated admins."""
import email.utils
import re
import sqlite3
import time
import sys
from pathlib import Path
sys.path[:0] = [str(Path(__file__).parents[1]), str(Path(__file__).parent)]
from test_selfservice import SelfServiceTestBase, PASSWORD, NEW_PASSWORD
from multiuser.accounts import AccountError, AccountService, ROLES
from multiuser.mailer import Mailer, MailerConfig


class GuidedSecurityTests(SelfServiceTestBase):
    def setUp(self):
        super().setUp()
        self.owner = self.accounts.get_user_by_username('owner')
        self.user = self.accounts.create_user(self.owner, 'guided-user', PASSWORD,
            email='guided@example.test', email_verified=True)
        self.sign_in(self.user)

    def code(self, purpose='reset'):
        return self.accounts.create_email_code(self.user['id'], purpose, self.user['email'])

    def test_preview_does_not_spend_code_but_final_reset_does(self):
        code = self.code()
        response = self.client.post('/api/account/code/check', json={'purpose':'reset','code':code}, headers=self.json_headers())
        self.assertEqual(response.status_code, 200)
        self.assertTrue(self.accounts.email_code_state(self.user['id'], 'reset')['pending'])
        response = self.client.post('/api/account/password/reset', json={'code':code,'password':NEW_PASSWORD,'confirm_password':NEW_PASSWORD}, headers=self.json_headers())
        self.assertEqual(response.status_code, 200)
        self.assertFalse(self.accounts.consume_email_code(self.user['id'], 'reset', code))

    def test_wrong_preview_counts_attempts_and_expired_or_other_purpose_fails(self):
        code = self.code()
        wrong = '999999' if code != '999999' else '888888'
        reply = self.client.post('/api/account/code/check', json={'purpose':'reset','code':wrong}, headers=self.json_headers())
        self.assertEqual(reply.status_code, 410)
        self.assertEqual(self.accounts.email_code_state(self.user['id'], 'reset')['attempts_left'], self.layer.config.email_code_attempts - 1)
        self.assertEqual(self.client.post('/api/account/code/check', json={'purpose':'reauth','code':code}, headers=self.json_headers()).status_code, 410)
        self.layer.db.execute('UPDATE user_email_tokens SET expires_at=? WHERE user_id=?', (time.time()-1,self.user['id']))
        self.assertEqual(self.client.post('/api/account/code/check', json={'purpose':'reset','code':code}, headers=self.json_headers()).status_code, 410)

    def test_preview_requires_session_csrf_and_allowed_purpose(self):
        self.assertEqual(self.client.post('/api/account/code/check', json={'purpose':'reset','code':'000000'}).status_code, 400)
        self.assertEqual(self.client.post('/api/account/code/check', json={'purpose':'invite','code':'000000'}, headers=self.json_headers()).status_code, 422)
        with self.client.session_transaction() as session:
            session.clear()
        self.assertEqual(self.client.post('/api/account/code/check', json={'purpose':'reset','code':'000000'}).status_code, 401)

    def test_identity_preview_rejects_wrong_password(self):
        self.assertEqual(self.client.post('/api/account/identity/check', json={'current_password':'wrong'}, headers=self.json_headers()).status_code, 403)
        self.assertEqual(self.client.post('/api/account/identity/check', json={'current_password':PASSWORD}, headers=self.json_headers()).status_code, 200)

    def test_sender_reports_expiry_and_resend_deadlines(self):
        before = time.time()
        response = self.client.post('/api/account/password/reset/send', json={}, headers=self.json_headers())
        self.assertEqual(response.status_code, 200)
        self.assertGreater(response.json['expires_at'], before)
        self.assertGreaterEqual(response.json['resend_at'], before+60)
        self.assertEqual(self.client.post('/api/account/password/reset/send', json={}, headers=self.json_headers()).status_code, 429)

    def test_public_reset_verifies_before_password_and_still_spends_code_once(self):
        code = self.code()
        response = self.client.post('/reset-password', data={'_csrf':self.csrf_now(),'identifier':self.user['username'],'code':code,'verify_only':'1'})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json['ok'])
        self.assertTrue(self.accounts.consume_email_code(self.user['id'], 'reset', code))
        self.assertEqual(self.client.post('/reset-password', data={'_csrf':self.csrf_now(),'identifier':self.user['username'],'code':code,'verify_only':'1'}).status_code, 410)

    def test_only_owner_and_user_roles_can_be_created_or_authorized(self):
        self.assertEqual(ROLES, ('owner','user'))
        with self.assertRaises(AccountError):
            self.accounts.create_user(self.owner, 'retired-admin', PASSWORD, role='admin')
        self.assertFalse(AccountService.can_view_admin(dict(self.user, role='admin')))
        self.assertFalse(AccountService.can_manage_user(dict(self.user, role='admin'), self.user))

    def test_migration_demotes_existing_admin_and_revokes_sessions(self):
        db = self.layer.db
        with db.connect() as conn:
            for table in ('users','user_invites'):
                for operation in ('insert','update'):
                    conn.execute(f'DROP TRIGGER {table}_reject_admin_{operation}')
            conn.execute("DELETE FROM multiuser_schema_migrations WHERE version=11")
            conn.execute("UPDATE users SET role='admin' WHERE id=?", (self.user['id'],))
        raw = self.accounts.create_session(self.accounts.get_user(self.user['id']))
        db._initialized = False
        db.initialize()
        self.assertEqual(self.accounts.get_user(self.user['id'])['role'], 'user')
        self.assertEqual(db.scalar('SELECT COUNT(*) FROM user_sessions WHERE user_id=? AND revoked_at=0',(self.user['id'],)),0)
        self.assertTrue(AccountService.can_view_admin(self.accounts.get_user(self.owner['id'])))
        with self.assertRaises(sqlite3.IntegrityError):
            db.execute("UPDATE users SET role='admin' WHERE id=?", (self.user['id'],))

    def test_all_mail_parts_include_matching_timestamp(self):
        mailer = Mailer(MailerConfig(from_address='music@example.test'))
        message = mailer._build('user@example.test','Security update','Body','<html><body><p>Body</p></body></html>')
        sent = email.utils.parsedate_to_datetime(message['Date'])
        stamp = sent.strftime('Sent on %d %b %Y at %H:%M:%S UTC')
        self.assertIn(stamp, message.get_body(preferencelist=('plain',)).get_content())
        html = message.get_body(preferencelist=('html',)).get_content()
        self.assertIn(stamp, html)
        self.assertLess(html.index(stamp), html.index('</body>'))
