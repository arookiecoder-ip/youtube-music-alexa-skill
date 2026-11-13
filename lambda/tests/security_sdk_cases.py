"""Actual SDK request dispatch, persistence boundaries, and authenticated HTTP."""
import copy
from pathlib import Path
import sys
from types import SimpleNamespace as Node
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import data
import lambda_function as skill
from mediaUtils import player
from skill_security import SkillAuthorizationError, persistence_key
from ask_sdk_core.response_helper import ResponseFactory
from ask_sdk_core.serialize import DefaultSerializer

SKILL = 'amzn1.ask.skill.33333333-3333-3333-3333-333333333333'


class SecurityCases(unittest.TestCase):
    def setUp(self):
        for name, value in [('ALEXA_SKILL_NAMESPACE', SKILL), ('MUSICBOX_EMAIL', 'user@example.test'),
                            ('DEFAULT_API_URL', 'https://music.example'), ('MUSICBOX_ALEXA_TOKEN', 'synthetic-secret')]:
            patcher = patch.object(data, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.binding = 'a' * 64
        self.rows = {}
        self.get = patch.object(skill.dynamodb_adapter, 'get_attributes',
                                side_effect=lambda request_envelope: copy.deepcopy(
                                    self.rows.get(skill.dynamodb_adapter.partition_keygen(request_envelope), {}))).start()
        self.save = patch.object(skill.dynamodb_adapter, 'save_attributes', side_effect=self.store).start()
        self.addCleanup(patch.stopall)
        self.identity_patch = patch.object(player.Api, '_get_json', side_effect=lambda *args:
                                           ({'binding': self.binding}, None))
        self.identity = self.identity_patch.start()

    def store(self, request_envelope, attributes):
        self.rows[skill.dynamodb_adapter.partition_keygen(request_envelope)] = copy.deepcopy(attributes)

    def event(self, name='AnnounceNowPlayingIntent', device='echo-a'):
        return {'version': '1.0', 'context': {'System': {
            'application': {'applicationId': SKILL}, 'user': {'userId': 'amazon-user'},
            'device': {'deviceId': device, 'supportedInterfaces': {'AudioPlayer': {}}}}},
            'request': {'type': 'IntentRequest', 'requestId': 'offline-request',
                        'timestamp': '2026-11-11T00:00:00Z', 'locale': 'en-US',
                        'intent': {'name': name, 'slots': {}}}}

    def invoke(self, event=None):
        return skill.lambda_handler(event or self.event(), None)

    def handler(self):
        info = dict(index=0, play_order=[0], offset_in_ms=4000,
                    stream_url='https://music.example/old?cap=previous-account', stream_url_video_id='AAAAAAAAAAA')
        attrs = dict(playback_info=info, playback_setting=dict(loop=False, shuffle=False),
                     playlist=[dict(title='Private A', artist='Artist', video_id='AAAAAAAAAAA', thumbnail=None)])
        envelope = Node(context=Node(system=Node(user=Node(user_id='amazon-user'), device=Node(device_id='echo-a'))),
                        request=Node(object_type='IntentRequest'), _musicbox_binding=self.binding)
        return Node(request_envelope=envelope, attributes_manager=Node(persistent_attributes={'amazon-user': attrs}),
                    response_builder=ResponseFactory())

    def test_device_and_generation_have_distinct_dynamodb_items(self):
        self.invoke(self.event(device='echo-a'))
        self.invoke(self.event(device='echo-b'))
        self.assertEqual(len(self.rows), 2)
        self.binding = 'b' * 64
        self.invoke(self.event(device='echo-a'))
        self.assertEqual(len(self.rows), 3)
        self.assertTrue(all(key.startswith('echo-v2:') for key in self.rows))

    def test_unverified_partition_cannot_read_a_legacy_user_bucket(self):
        envelope = self.handler().request_envelope
        del envelope._musicbox_binding
        with self.assertRaises(SkillAuthorizationError):
            persistence_key(envelope)
    def test_skill_id_is_not_required_and_actual_application_partitions_state(self):
        event = self.event()
        self.invoke(event)
        event['context']['System']['application']['applicationId'] = 'another-skill'
        self.invoke(event)
        self.assertEqual(len(self.rows), 2)

    def test_repair_never_loads_previous_accounts_saved_metadata(self):
        self.invoke()
        bucket = next(iter(self.rows.values()))['amazon-user']
        bucket['playlist'] = [dict(title='Private A', artist='Artist', video_id='AAAAAAAAAAA', thumbnail=None)]
        bucket['playback_info']['play_order'] = [0]
        self.assertIn('Private A', self.invoke()['response']['outputSpeech']['ssml'])
        self.binding = 'b' * 64
        response = self.invoke()['response']
        self.assertNotIn('Private A', response['outputSpeech']['ssml'])
        self.assertIn('Nothing is playing', response['outputSpeech']['ssml'])

    def test_loop_commands_update_the_settings_used_by_playback(self):
        self.invoke(self.event('AMAZON.LoopOnIntent'))
        bucket = next(iter(self.rows.values()))['amazon-user']
        self.assertTrue(bucket['playback_setting']['loop'])
        self.assertNotIn('loop', bucket['playback_info'])
        self.invoke(self.event('AMAZON.LoopOffIntent'))
        self.assertFalse(next(iter(self.rows.values()))['amazon-user']['playback_setting']['loop'])

    def test_declared_navigation_and_repeat_intents_have_real_handlers(self):
        response = self.invoke(self.event('AMAZON.NavigateHomeIntent'))['response']
        self.assertIn('Welcome to Music Box', response['outputSpeech']['ssml'])
        response = self.invoke(self.event('AMAZON.RepeatIntent'))['response']
        self.assertIn('nothing to resume', response['outputSpeech']['ssml'])

    def test_unlinked_request_never_reads_or_saves_persistence(self):
        self.identity.side_effect = None
        self.identity.return_value = (None, SkillAuthorizationError('unlinked'))
        result = self.invoke()
        self.assertIn('access was denied', result['response']['outputSpeech']['ssml'])
        self.get.assert_not_called()
        self.save.assert_not_called()
        directives = result['response']['directives']
        self.assertEqual([d['type'] for d in directives], ['AudioPlayer.Stop', 'AudioPlayer.ClearQueue'])
        self.assertEqual(directives[1]['clearBehavior'], 'CLEAR_ALL')

    def test_permission_card_is_only_shown_when_amazon_permission_is_missing(self):
        self.identity.side_effect = None
        self.identity.return_value = (None, SkillAuthorizationError('permission', code='amazon_permission_required'))
        self.assertEqual(self.invoke()['response']['card']['permissions'], ['alexa::profile:email:read'])
        self.identity.return_value = (None, SkillAuthorizationError('disabled'))
        self.assertNotIn('card', self.invoke()['response'])

    def test_late_callback_from_previous_binding_cannot_load_new_state(self):
        event = self.event()
        event['request'] = dict(type='AudioPlayer.PlaybackStarted', requestId='old-callback',
                                timestamp='2026-11-11T00:00:00Z', token='0|AAAAAAAAAAA|old|b' + 'b' * 64,
                                offsetInMilliseconds=0)
        result = self.invoke(event)['response']
        self.assertFalse(result.get('directives'))
        self.assertNotIn('outputSpeech', result)
        self.get.assert_not_called()
        self.save.assert_not_called()

    def test_auth_denied_started_event_stops_without_speech_or_play_directive(self):
        event = self.event()
        event['request'] = dict(type='AudioPlayer.PlaybackStarted', requestId='denied-start',
            timestamp='2026-11-12T00:00:00Z', token='0|AAAAAAAAAAA|attempt|b'+self.binding,
            offsetInMilliseconds=0)
        self.identity.side_effect = None
        self.identity.return_value = (None, SkillAuthorizationError('Reconnect Amazon', code='amazon_link_required'))
        result = self.invoke(event)['response']
        self.assertEqual([d['type'] for d in result['directives']], ['AudioPlayer.Stop','AudioPlayer.ClearQueue'])
        self.assertNotIn('outputSpeech', result)
        self.assertNotIn('shouldEndSession', result)
        self.get.assert_not_called()
        self.save.assert_not_called()

    def test_denied_queue_callback_stops_instead_of_enqueuing_another_song(self):
        for kind in ('AudioPlayer.PlaybackNearlyFinished','AudioPlayer.PlaybackFinished','AudioPlayer.PlaybackFailed'):
            with self.subTest(kind=kind):
                event = self.event()
                event['request'] = dict(type=kind, requestId='denied-queue', timestamp='2026-11-12T00:00:00Z',
                    token='0|AAAAAAAAAAA|attempt|b'+self.binding, offsetInMilliseconds=0)
                self.identity.side_effect = None
                self.identity.return_value = (None, SkillAuthorizationError('Reconnect Amazon', code='amazon_link_required'))
                result = self.invoke(event)['response']
                self.assertEqual([d['type'] for d in result['directives']], ['AudioPlayer.Stop','AudioPlayer.ClearQueue'])
                self.assertNotIn('outputSpeech', result)
                self.assertNotIn('shouldEndSession', result)

    def test_stale_auth_denial_cannot_stop_or_save_over_newer_playback(self):
        self.identity.side_effect = None
        self.identity.return_value = (None, SkillAuthorizationError('superseded', code='stale_alexa_request'))
        result = self.invoke()['response']
        self.assertFalse(result.get('directives'))
        self.assertNotIn('outputSpeech', result)
        self.get.assert_not_called()
        self.save.assert_not_called()

    def test_current_binding_callback_persists_only_its_device_state(self):
        self.invoke()
        bucket = next(iter(self.rows.values()))['amazon-user']
        bucket['playlist'] = [dict(title='Current', artist='Artist', video_id='AAAAAAAAAAA', thumbnail=None)]
        token = '0|AAAAAAAAAAA|attempt|b' + self.binding
        bucket['playback_info'].update(play_order=[0], current_token=token)
        event = self.event()
        event['request'] = dict(type='AudioPlayer.PlaybackStopped', requestId='current-callback',
                                timestamp='2026-11-11T00:00:00Z', token=token, offsetInMilliseconds=9000)
        with patch.object(player, '_notify_server'):
            response = self.invoke(event)['response']
        self.assertNotIn('outputSpeech', response)
        updated = next(iter(self.rows.values()))['amazon-user']
        self.assertEqual(updated['playback_info']['offset_in_ms'], 9000)

    def test_linking_does_not_read_or_rewrite_previous_persistence(self):
        event = self.event('LinkAccountIntent')
        event['request']['intent']['slots'] = {'pairCode': {'name': 'pairCode', 'value': '12345678'}}
        with patch.object(player.Api, 'link_account', return_value=True):
            response = self.invoke(event)['response']
            self.assertIn('no longer available', response['outputSpeech']['ssml'])
            self.assertFalse(response.get('directives'))
        self.identity.assert_not_called()
        self.get.assert_not_called()
        self.save.assert_not_called()

    def test_voice_url_override_is_disabled_without_accessing_saved_state(self):
        event = self.event('SetApiurlIntent')
        event['request']['intent']['slots'] = {'apiUrl': {'name': 'apiUrl', 'value': 'http://attacker.invalid'.encode().hex()}}
        self.assertIn('administrator', self.invoke(event)['response']['outputSpeech']['ssml'])
        self.get.assert_not_called()
        self.save.assert_not_called()

    def test_resume_and_seek_fetch_new_capabilities_and_scope_tokens(self):
        for action in ('resume', 'seek'):
            hi = self.handler()
            with patch.object(player.Api, 'get_armed_play', return_value=(None, Exception('not found'))), \
                    patch.object(player.Api, 'get_stream', return_value=(Node(audio_url='https://music.example/fresh'), None)) as fresh:
                response = (player.Controller.resume(hi, is_playback=True) if action == 'resume'
                            else player.Controller.seek(hi, 9000))
            fresh.assert_called_once()
            stream = DefaultSerializer().serialize(response)['directives'][0]['audioItem']['stream']
            self.assertEqual(stream['url'], 'https://music.example/fresh')
            self.assertTrue(stream['token'].endswith('|b' + self.binding))

    def test_resume_authorization_failure_never_uses_cached_url(self):
        hi = self.handler()
        with patch.object(player.Api, 'get_armed_play', return_value=(None, SkillAuthorizationError('unlinked'))), \
                patch.object(player.Api, 'get_stream') as fresh:
            response = player.Controller.resume(hi, is_playback=True)
        self.assertEqual([d.object_type for d in response.directives], ['AudioPlayer.Stop', 'AudioPlayer.ClearQueue'])
        fresh.assert_not_called()

    def test_fresh_capability_denial_never_replays_cached_audio(self):
        for action in ('resume', 'seek'):
            hi = self.handler()
            with patch.object(player.Api, 'get_armed_play', return_value=(None, Exception('not found'))), \
                    patch.object(player.Api, 'get_stream', return_value=(None, SkillAuthorizationError('unlinked'))):
                response = (player.Controller.resume(hi, is_playback=True) if action == 'resume'
                            else player.Controller.seek(hi, 9000))
            self.assertEqual([d.object_type for d in response.directives], ['AudioPlayer.Stop', 'AudioPlayer.ClearQueue'])

    def test_real_http_denial_during_resume_cannot_use_saved_audio_or_lookup_fallback(self):
        import json
        self.invoke()
        bucket = next(iter(self.rows.values()))['amazon-user']
        bucket['playlist'] = [dict(title='Selected',artist='Artist',video_id='AAAAAAAAAAA',thumbnail=None)]
        bucket['playback_info'].update(index=0,play_order=[0],stream_url='https://music.example/stale',
                                      stream_url_video_id='AAAAAAAAAAA')
        self.identity_patch.stop()
        paths=[]
        def http(method,url,**kwargs):
            paths.append(url.split('/')[3])
            if '/api/alexa/identity/' in url:
                return Node(status=200,data=json.dumps({'binding':self.binding}).encode())
            return Node(status=403,data=json.dumps({'error':{'code':'amazon_link_required'}}).encode())
        with patch.object(player.http,'request',side_effect=http):
            response=self.invoke(self.event('AMAZON.ResumeIntent'))['response']
        self.assertEqual([d['type'] for d in response['directives']], ['AudioPlayer.Stop','AudioPlayer.ClearQueue'])
        self.assertEqual(paths,['api','armed_play'])

    def test_denied_state_post_does_not_expand_radio_or_swallow_authentication(self):
        import json
        self.identity_patch.stop()
        requests=[]
        def http(method,url,**kwargs):
            requests.append((method,url))
            if method=='GET':
                return Node(status=200,data=json.dumps({'binding':self.binding}).encode())
            return Node(status=403,data=json.dumps({'error':{'code':'amazon_link_required'}}).encode())
        event=self.event()
        event['request']=dict(type='AudioPlayer.PlaybackStarted',requestId='denied-post',
            timestamp='2026-11-12T00:00:00Z',token='0|AAAAAAAAAAA|attempt|b'+self.binding,offsetInMilliseconds=0)
        with patch.object(player.http,'request',side_effect=http), \
                patch.object(player.Controller,'expand_radio_queue') as expand:
            response=self.invoke(event)['response']
        self.assertEqual([d['type'] for d in response['directives']], ['AudioPlayer.Stop','AudioPlayer.ClearQueue'])
        expand.assert_not_called()
        self.assertEqual(len(requests),2)

    def test_http_uses_only_configured_https_origin_and_never_follows_redirects(self):
        # Stop the dispatch mock; exercise real URL and HTTP helpers below.
        self.identity_patch.stop()
        with patch.object(data, 'DEFAULT_API_URL', 'https://music.example'), \
                patch.object(data, 'MUSICBOX_ALEXA_TOKEN', 'synthetic-secret'), \
                patch.object(data, 'ALEXA_SKILL_NAMESPACE', SKILL):
            hi = self.handler()
            hi.attributes_manager.persistent_attributes['amazon-user']['api_url'] = 'http://attacker.invalid'
            with patch.object(player.http, 'request', return_value=Node(status=302, data=b'')) as send:
                _, error = player.Api._get_json(hi, 'get_stream', {'video_id': 'AAAAAAAAAAA'})
                self.assertIsNotNone(error)
                self.assertTrue(send.call_args.args[1].startswith('https://music.example/'))
                self.assertFalse(send.call_args.kwargs['redirect'])
                self.assertEqual(send.call_args.kwargs['headers']['X-Alexa-Binding'], self.binding)
                self.assertFalse(player.Api.link_account(hi, '12345678'))
                self.assertFalse(send.call_args.kwargs['redirect'])
                player._notify_server(hi, 'finished')
                self.assertFalse(send.call_args.kwargs['redirect'])

    def test_bundled_startup_builds_real_sdk_play_directive_without_song_fetch(self):
        event = self.event('PlaySongIntent')
        event['request']['intent']['slots'] = {'songName': {'name': 'songName', 'value': 'mbsilent app selection'}}
        # Use the exact marker/phrase recognized by the production handler.
        event['request']['intent']['slots']['songName']['value'] = player.SILENT_PLAY_MARKER + ' app selection'
        metadata = dict(title='Selected song', artist='Artist', video_id='AAAAAAAAAAA',
                        thumbnail={'url':'https://images.example/art', 'width':0, 'height':0}, duration_ms=180000)
        earlier = dict(metadata, title='Earlier occurrence')
        other = dict(metadata, title='Other song', video_id='BBBBBBBBBBB')
        startup = dict(song_info={'metadata':metadata, 'stream':{'audio_url':'https://music.example/audio'}},
                       playlist=[earlier, other, metadata], queue_index=2)
        paths = []
        def api(handler, path, params):
            paths.append(path)
            if path == 'api/alexa/identity':
                return {'binding': self.binding}, None
            if path == 'armed_play':
                return dict(video_id='AAAAAAAAAAA', offset_ms=1234, kind='play', startup=startup), None
            raise AssertionError('Unexpected startup HTTP call: '+path)
        self.identity.side_effect = api
        response = self.invoke(event)['response']
        directive = response['directives'][0]
        self.assertEqual(directive['type'], 'AudioPlayer.Play')
        self.assertEqual(directive['audioItem']['stream']['url'], 'https://music.example/audio')
        self.assertEqual(directive['audioItem']['stream']['offsetInMilliseconds'], 1234)
        self.assertTrue(directive['audioItem']['stream']['token'].startswith('2|AAAAAAAAAAA|'))
        self.assertEqual(paths, ['api/alexa/identity', 'armed_play'])

    def test_invalid_configured_origins_fail_before_http(self):
        hi = self.handler()
        with patch.object(player.http, 'request') as send:
            for url in ('http://music.example', 'https://user:pass@music.example',
                        'https://music.example?redirect=evil', 'https://music.example/#evil',
                        'https://music.example:bad', 'https://music.example\n'):
                with patch.object(data, 'DEFAULT_API_URL', url):
                    self.assertIsNotNone(player.Attributes.get_api_url(hi)[1])
            send.assert_not_called()


if __name__ == '__main__':
    unittest.main()
