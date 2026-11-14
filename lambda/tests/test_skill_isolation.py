"""The second skill must identify itself and refuse other skills' requests."""
import os
import sys
import unittest
from unittest import mock

from test_resume_armed_play import player, data, _node

SKILL = 'amzn1.ask.skill.33333333-3333-3333-3333-333333333333'


def _identity(user='alexa-user-one', device='echo-device-one'):
    return _node(request_envelope=_node(context=_node(system=_node(
        user=_node(user_id=user), device=_node(device_id=device)))))


def _event(application=SKILL):
    return {'context': {'System': {'application': {'applicationId': application}}}}


def _lambda_function():
    """Import the real handler after the shared player fixture's stubs are up.

    test_resume_armed_play installs a minimal urllib3 module because player.py
    only needs a placeholder; botocore, imported by lambda_function, needs the
    real package. Removing only that stub from import resolution leaves the
    fixture's already-imported reference intact.
    """
    for name in list(sys.modules):
        if not (name == 'urllib3' or name == 'dacite'
                or name == 'ask_sdk_core' or name.startswith('ask_sdk_core.')
                or name == 'ask_sdk_model' or name.startswith('ask_sdk_model.')):
            continue
        module = sys.modules[name]
        if getattr(module, '__file__', None) is None:
            del sys.modules[name]
    # The real module builds a DynamoDB resource at import time; a region and
    # table name exist in the hosted runtime but not in a bare test process.
    os.environ.setdefault('AWS_DEFAULT_REGION', 'us-east-1')
    os.environ.setdefault('DYNAMODB_PERSISTENCE_REGION', 'us-east-1')
    os.environ.setdefault('DYNAMODB_PERSISTENCE_TABLE_NAME', 'skill-isolation-test')
    import lambda_function
    return lambda_function


class SkillIdentityHeaderTests(unittest.TestCase):
    def test_shared_client_sends_only_per_account_credentials(self):
        with mock.patch.object(data, 'MUSICBOX_ALEXA_TOKEN', 'user-token'), mock.patch.object(data, 'MUSICBOX_EMAIL', 'user@example.test'):
            headers = player.skill_identity_headers(_identity())
        self.assertEqual(headers['Authorization'], 'Bearer user-token')
        self.assertEqual(headers['X-MusicBox-Email'], 'user@example.test')
        self.assertNotIn('X-Api-Key', headers)
        self.assertNotIn('X-Alexa-Skill-Id', headers)

    def test_stable_account_id_is_forwarded(self):
        with mock.patch.object(data, 'MUSICBOX_ACCOUNT_ID', 'stable-account-id'):
            headers = player.skill_identity_headers(_identity())
            self.assertEqual(headers['X-MusicBox-Account-Id'], 'stable-account-id')
            self.assertNotIn('X-MusicBox-Email', headers)

    def test_the_paired_user_and_device_are_forwarded_unchanged(self):
        with mock.patch.object(data, 'ALEXA_SKILL_NAMESPACE', SKILL):
            first = player.skill_identity_headers(_identity('a', 'echo-a'))
            second = player.skill_identity_headers(_identity('b', 'echo-b'))
        self.assertEqual(first['X-Alexa-User-Id'], 'a')
        self.assertEqual(first['X-Alexa-Device-Id'], 'echo-a')
        self.assertEqual(second['X-Alexa-User-Id'], 'b')
        self.assertNotEqual(first['X-Alexa-Device-Id'], second['X-Alexa-Device-Id'])

    def test_request_id_follows_preflight_and_action_for_same_envelope(self):
        request = _identity()
        request.request_envelope.request = _node(request_id='amzn1.echo-api.request.request-a')
        first = player.skill_identity_headers(request)
        second = player.skill_identity_headers(request)
        self.assertEqual(first['X-Alexa-Request-Id'], 'amzn1.echo-api.request.request-a')
        self.assertEqual(first['X-Alexa-Request-Id'], second['X-Alexa-Request-Id'])

    def test_request_timestamp_and_audio_identity_are_forwarded_for_failure_fences(self):
        request = _identity()
        request.request_envelope.request = _node(object_type='AudioPlayer.PlaybackStarted',
            timestamp='2026-11-12T10:00:00Z', token='0|AAAAAAAAAAA|attempt')
        headers = player.skill_identity_headers(request)
        self.assertEqual(headers['X-Alexa-Request-Timestamp'], '2026-11-12T10:00:00Z')
        self.assertEqual(headers['X-Alexa-Request-Type'], 'AudioPlayer.PlaybackStarted')
        self.assertEqual(headers['X-Alexa-Playback-Token'], '0|AAAAAAAAAAA|attempt')

    def test_a_missing_device_id_does_not_become_every_device(self):
        headers = player.skill_identity_headers(_identity('a', ''))
        self.assertEqual(headers['X-Alexa-Device-Id'], '')


class ApplicationConfigurationTests(unittest.TestCase):
    def test_skill_id_is_not_a_required_configuration(self):
        lambda_function = _lambda_function()
        self.assertFalse(hasattr(data, 'ALEXA_SKILL_ID'))
        with mock.patch.object(lambda_function, '_sdk_lambda_handler', return_value='ok') as handler:
            self.assertEqual(lambda_function.lambda_handler(_event('any-private-skill'), None), 'ok')
            handler.assert_called_once()

if __name__ == '__main__':
    unittest.main()
