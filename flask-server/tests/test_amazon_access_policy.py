"""Amazon web sign-in may use another email without bypassing account access."""
import ast
from pathlib import Path
import sys
sys.path[:0] = [str(Path(__file__).parents[1]), str(Path(__file__).parent)]
from test_multiuser import MultiUserTestBase


class AmazonAccessPolicyTests(MultiUserTestBase):
    def setUp(self):
        super().setUp()
        owner=self.layer.accounts.get_user_by_username('owner')
        self.user=self.layer.accounts.create_user(owner,'amazon-person','Person!Password12',
            email='musicbox@example.test',email_verified=True)
        self.layer.alexa_access.set_enabled(self.user['id'], True)
        source=Path(__file__).parents[1]/'server.py'
        function=next(node for node in ast.walk(ast.parse(source.read_text()))
                      if isinstance(node,ast.FunctionDef) and node.name=='_amazon_access_policy')
        namespace={'_multiuser_layer':self.layer,
                   '_identity_account_id':lambda identity:identity.removeprefix('user:')}
        exec(compile(ast.Module(body=[function],type_ignores=[]),str(source),'exec'),namespace)
        self.policy=namespace['_amazon_access_policy']

    def test_another_amazon_email_and_mobile_number_are_allowed(self):
        for identifier in ('different-amazon@example.test','+919876543210','9876543210'):
            self.assertIsNone(self.policy('user:'+self.user['id'],identifier))

    def test_disabled_access_still_blocks_another_amazon_account(self):
        self.layer.alexa_access.set_enabled(self.user['id'],False)
        self.assertIsNotNone(self.policy('user:'+self.user['id'],'different-amazon@example.test'))

    def test_restricted_unverified_or_missing_musicbox_user_is_rejected(self):
        for update in ("status='disabled'","must_change_password=1","email_verified=0"):
            self.layer.db.execute('UPDATE users SET '+update+' WHERE id=?',(self.user['id'],))
            self.assertIsNotNone(self.policy('user:'+self.user['id'],'different-amazon@example.test'))
            self.layer.db.execute("UPDATE users SET status='active',must_change_password=0,email_verified=1 WHERE id=?",(self.user['id'],))
        self.assertIsNotNone(self.policy('user:missing-user','different-amazon@example.test'))

    def test_another_musicbox_user_does_not_inherit_access(self):
        owner=self.layer.accounts.get_user_by_username('owner')
        other=self.layer.accounts.create_user(owner,'other-person','Person!Password12',email='other@example.test',email_verified=True)
        self.assertIsNotNone(self.policy('user:'+other['id'],'different-amazon@example.test'))
        self.assertIsNone(self.policy('user:'+self.user['id'],'different-amazon@example.test'))
