"""Active playback sessions renew inactivity expiry without bypassing revocation."""
import time
from unittest import mock
from test_multiuser import MultiUserTestBase


class ActiveSessionRenewalTests(MultiUserTestBase):
    def setUp(self):
        super().setUp()
        self.accounts=self.layer.accounts
        self.owner=self.accounts.get_user_by_username('owner')
        self.now=time.time()
        with mock.patch('multiuser.accounts._now',return_value=self.now):
            self.sid=self.accounts.create_session(self.owner)

    def validate(self,hours):
        with mock.patch('multiuser.accounts._now',return_value=self.now+hours*3600):
            return self.accounts.validate_session(self.sid)

    def test_regular_activity_survives_initial_twelve_hour_deadline(self):
        self.assertIsNotNone(self.validate(11)[0])
        session,user=self.validate(13)
        self.assertIsNotNone(user)
        self.assertGreater(session['expires_at'],self.now+24*3600)

    def test_idle_session_expires_and_is_not_resurrected(self):
        self.assertEqual(self.validate(13),(None,None))

    def test_absolute_limit_still_expires_an_active_session(self):
        for hour in range(1,7*24):self.assertIsNotNone(self.validate(hour)[0])
        self.assertEqual(self.validate(7*24+1),(None,None))

    def test_revoked_session_never_renews(self):
        record,_=self.validate(1)
        self.accounts.revoke_session(record['sid_hash'])
        self.assertEqual(self.validate(2),(None,None))
