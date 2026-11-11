"""Physical-browser capability isolation and single-use Echo pairing."""
import os
import sys
import time
from pathlib import Path
from unittest import TestCase, mock
sys.path[:0]=[str(Path(__file__).parent),str(Path(__file__).parents[1])]
from browser_coordination import BrowserBroker
from youtube_browser_session import YouTubeBrowserSessionManager
from test_selfservice import SelfServiceTestBase


class BrokerTests(TestCase):
    def test_another_owner_cannot_start_or_stop_the_active_attempt(self):
        broker=BrowserBroker(); sent=[]
        token=broker.command('a','/google/start',None,lambda t:sent.append(t) or {})['attempt_id']
        self.assertEqual(broker.command('b','/interactive/start',None,lambda t:self.fail('must not send'))['state'],'busy')
        with self.assertRaises(PermissionError):broker.command('b','/google/stop',token,lambda t:self.fail('must not send'))
        self.assertEqual(broker.owner,'a')

    def test_replacement_invalidates_old_tab_capability(self):
        broker=BrowserBroker()
        old=broker.command('a','/google/start',None,lambda t:{})['attempt_id']
        broker.command('a','/google/start',None,lambda t:{})
        with self.assertRaises(PermissionError):broker.command('a','/google/capture',old,lambda t:self.fail('must not send'))

    def test_expired_browser_lease_admits_another_owner(self):
        broker=BrowserBroker();broker.command('a','/google/start',None,lambda t:{})
        broker.expires=0
        self.assertEqual(broker.command('b','/google/start',None,lambda t:{})['attempt_id'],broker.token)
        self.assertEqual(broker.owner,'b')

    def test_unrelated_revoke_never_closes_global_browser(self):
        controller=mock.Mock();controller.call.return_value={'state':'waiting_for_login'}
        manager=YouTubeBrowserSessionManager(controller,lambda *a:None)
        manager.start('a');controller.reset_mock();manager.revoke('b')
        controller.call.assert_not_called()

    def test_expired_manager_claim_is_released_before_admission(self):
        now=[0.];controller=mock.Mock();controller.call.return_value={'state':'waiting_for_login'}
        manager=YouTubeBrowserSessionManager(controller,lambda *a:None,lease_ttl=10,clock=lambda:now[0])
        manager.start('a');now[0]=11
        self.assertIsNotNone(manager.start('b')[0])


class EchoPairingTests(SelfServiceTestBase):
    def test_pairing_is_subject_device_bound_and_single_use(self):
        user=self.accounts.get_user_by_username('owner');identity=self.layer.alexa_identity
        code=identity.issue(user)
        self.assertTrue(identity.pair(code,'alexa-user','device-a'))
        self.assertFalse(identity.pair(code,'other','device-b'))
        self.assertEqual(identity.resolve('alexa-user','device-a')['id'],user['id'])
        self.assertIsNone(identity.resolve('alexa-user','device-b'))

    def test_pair_code_and_stream_capability_die_on_password_change(self):
        user=self.accounts.get_user_by_username('owner');identity=self.layer.alexa_identity
        code=identity.issue(user);cap=identity.stream_token(user['id'],'dQw4w9WgXcQ')
        self.accounts.change_own_password(user,'Owner!Passw0rd','Changed!Passw0rd')
        self.assertFalse(identity.pair(code,'alexa-user','device'))
        self.assertIsNone(identity.stream_user(cap,'dQw4w9WgXcQ'))

    def test_stream_capability_is_video_bound_and_tamper_resistant(self):
        user=self.accounts.get_user_by_username('owner');identity=self.layer.alexa_identity
        self.layer.alexa_access.set_enabled(user['id'], True)
        token=identity.stream_token(user['id'],'dQw4w9WgXcQ')
        self.assertEqual(identity.stream_user(token,'dQw4w9WgXcQ')['id'],user['id'])
        self.assertIsNone(identity.stream_user(token,'aaaaaaaaaaa'))
        self.assertIsNone(identity.stream_user(token+'corrupt','dQw4w9WgXcQ'))
