"""Cold-entry ordering and request isolation using the real lightweight modules."""
import json
import sys
import threading
import unittest
from concurrent.futures import Future
from pathlib import Path
from types import SimpleNamespace as Node
from unittest import mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import data
import skill_bootstrap as bootstrap
import lambda_function as entry


def event():
    return {'context':{'System':{'user':{'userId':'subject'},'device':{'deviceId':'echo'},
            'apiAccessToken':'synthetic-profile-proof','apiEndpoint':'https://api.amazonalexa.com'}},
        'request':{'type':'IntentRequest','requestId':'unique-request',
            'timestamp':'2026-11-20T12:00:00Z','intent':{'name':'PlaySongIntent',
            'slots':{'songName':{'value':'silentmode the app selection'}}}}}

class BootstrapCases(unittest.TestCase):
    def setUp(self):
        for name,value in [('DEFAULT_API_URL','https://music.example'),('MUSICBOX_ALEXA_TOKEN','synthetic-token'),('MUSICBOX_EMAIL','test@example.test')]:
            p=mock.patch.object(data,name,value);p.start();self.addCleanup(p.stop)
        bootstrap.clear();self.addCleanup(bootstrap.clear)

    def test_sdk_loading_overlaps_auth_and_result_is_consumed_once(self):
        started,release=threading.Event(),threading.Event();response=object()
        def fetch(*args):
            started.set();self.assertTrue(release.wait(2));return response
        def delegate(*args):
            self.assertTrue(started.wait(2))
            # The runtime loader/delegate must be reached while auth is blocked.
            release.set()
            future=bootstrap.take('unique-request','https://music.example',{'include_play':'1'})
            self.assertIs(future.result(),response)
            self.assertIsNone(bootstrap.take('unique-request','https://music.example',{'include_play':'1'}))
            return 'played'
        with mock.patch.object(entry,'_runtime',None),mock.patch.object(entry,'_sdk_lambda_handler',side_effect=delegate),mock.patch.object(bootstrap,'_fetch',side_effect=fetch) as call:
            self.assertEqual(entry.lambda_handler(event(),Node(aws_request_id='lambda-request')),'played')
        call.assert_called_once()

    def test_warm_runtime_does_not_create_another_auth_request(self):
        with mock.patch.object(entry,'_runtime',object()),mock.patch.object(entry,'_sdk_lambda_handler',return_value='warm'),mock.patch.object(bootstrap,'_fetch') as fetch:
            self.assertEqual(entry.lambda_handler(event(),Node(aws_request_id='request')),'warm')
        fetch.assert_not_called()

    def test_non_runtime_call_keeps_existing_dispatch_contract(self):
        with mock.patch.object(entry,'_runtime',None),mock.patch.object(entry,'_sdk_lambda_handler',return_value='test'),mock.patch.object(bootstrap,'_fetch') as fetch:
            self.assertEqual(entry.lambda_handler(event(),None),'test')
        fetch.assert_not_called()

    def test_failure_cannot_reuse_a_previous_requests_future(self):
        def delegate(*args):
            raise RuntimeError('synthetic loader failure')
        with mock.patch.object(entry,'_runtime',None),mock.patch.object(entry,'_sdk_lambda_handler',side_effect=delegate),mock.patch.object(bootstrap,'_fetch',return_value=object()):
            with self.assertRaises(RuntimeError):entry.lambda_handler(event(),Node(aws_request_id='request'))
        self.assertIsNone(bootstrap.take('unique-request','https://music.example',{'include_play':'1'}))

    def test_result_cannot_cross_request_origin_or_action(self):
        for request_id,url,params in [('other','https://music.example',{'include_play':'1'}),('unique-request','https://other.example',{'include_play':'1'}),('unique-request','https://music.example',{'include_resume':'1'})]:
            future=Future();future.set_result(object())
            bootstrap._pending.item=('unique-request','https://music.example',{'include_play':'1'},future)
            self.assertIsNone(bootstrap.take(request_id,url,params))
            self.assertIsNone(bootstrap.take('unique-request','https://music.example',{'include_play':'1'}))

    def test_fetch_is_bounded_without_redirects_or_retries(self):
        client=mock.Mock()
        with mock.patch.object(bootstrap,'http_client',return_value=client):
            bootstrap._fetch('https://music.example',{'include_play':'1'},{'Authorization':'Bearer synthetic'})
        args=client.request.call_args
        self.assertEqual(args.args[:2],('GET','https://music.example/api/alexa/identity/?include_play=1'))
        self.assertFalse(args.kwargs['redirect']);self.assertFalse(args.kwargs['retries'])
        self.assertEqual(args.kwargs['timeout'].total,3)

    def test_bad_configuration_or_missing_identity_never_prefetches(self):
        for url in ('http://music.example','https://user:password@music.example','https://music.example/?token=private','https://music.example:invalid'):
            with mock.patch.object(data,'DEFAULT_API_URL',url):self.assertFalse(bootstrap.can_prefetch(event()))
        for field in ('apiAccessToken','device','user'):
            value=event();del value['context']['System'][field]
            self.assertFalse(bootstrap.can_prefetch(value))

    def test_non_state_events_and_settings_never_prefetch(self):
        for kind in ('System.ExceptionEncountered','SessionEndedRequest'):
            value=event();value['request']['type']=kind;self.assertFalse(bootstrap.can_prefetch(value))
        for name in ('LinkAccountIntent','SetApiurlIntent'):
            value=event();value['request']['intent']['name']=name;self.assertFalse(bootstrap.can_prefetch(value))

    def test_action_selection_does_not_misinterpret_voice_search(self):
        self.assertEqual(bootstrap.identity_params('IntentRequest','PlaySongIntent','silentmode THE APP SELECTION'),{'include_play':'1'})
        self.assertEqual(bootstrap.identity_params('IntentRequest','PlaySongIntent','the app selection remix'),{})
        self.assertEqual(bootstrap.identity_params('IntentRequest','AMAZON.ResumeIntent'),{'include_resume':'1'})
        self.assertEqual(bootstrap.identity_params('IntentRequest','AMAZON.PauseIntent'),{})

    def test_initial_headers_preserve_event_and_device_fences(self):
        value=event();value['request'].update(type='AudioPlayer.PlaybackFailed',token='original-token',currentPlaybackState={'token':'current-token'})
        headers=bootstrap.identity_headers(value)
        self.assertEqual(headers['X-Alexa-Playback-Token'],'original-token')
        self.assertEqual(headers['X-Alexa-Current-Playback-Token'],'current-token')
        self.assertEqual(headers['X-Alexa-Device-Id'],'echo')
        self.assertEqual(headers['X-Alexa-Request-Timestamp'],'2026-11-20T12:00:00Z')
        self.assertNotIn('X-Api-Key',headers);self.assertNotIn('X-Alexa-Binding',headers)

if __name__=='__main__':unittest.main()
