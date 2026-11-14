"""The arm payload is reused once, only within its own request and video."""
import unittest
from unittest import mock
from test_resume_armed_play import player, _node, NEW_SONG, OLD_SONG, _raw


def handler():
    return _node(request_envelope=_node())


def payload(video=NEW_SONG):
    return {'video_id': video, 'offset_ms': 1234, 'kind': 'play',
        'startup': {'song_info': {'metadata': _raw(video),
            'stream': {'audio_url': 'https://musicbox.example/audio'}},
            'playlist': [_raw(video)]}}


class StartupBundleTests(unittest.TestCase):
    def test_direct_play_uses_one_http_call_and_parses_bundled_data(self):
        request, response, parsed = handler(), payload(), object()
        with mock.patch.object(player.Api, '_get_json', return_value=(response, None)) as get, \
                mock.patch.object(player, 'from_dict', return_value=parsed) as parse:
            armed, error = player.Api.get_armed_play(request)
            self.assertIsNone(error)
            self.assertEqual(armed[:2], (NEW_SONG, 1234))
            song, error = player.Api.stream_video(request, NEW_SONG)
        self.assertIs(song, parsed)
        self.assertIsNone(error)
        get.assert_called_once_with(request, 'armed_play', {'include_stream': '1'})
        parse.assert_called_once_with(player.player_models.SongInfoList, response['startup'])
        self.assertIsNone(request.request_envelope._musicbox_startup)

    def test_other_request_cannot_use_bundled_data(self):
        request, other = handler(), handler()
        with mock.patch.object(player.Api, '_get_json', return_value=(payload(), None)):
            player.Api.get_armed_play(request)
        with mock.patch.object(player.Api, '_get_json', return_value=({}, None)) as get, \
                mock.patch.object(player, 'from_dict', return_value=object()):
            player.Api.stream_video(other, NEW_SONG)
        get.assert_called_once_with(other, 'stream_video', {'video_id': NEW_SONG})

    def test_different_video_fetches_its_own_data(self):
        request = handler()
        request.request_envelope._musicbox_startup = payload()['startup']
        with mock.patch.object(player.Api, '_get_json', return_value=({}, None)) as get, \
                mock.patch.object(player, 'from_dict', return_value=object()):
            player.Api.stream_video(request, OLD_SONG)
        get.assert_called_once_with(request, 'stream_video', {'video_id': OLD_SONG})
        self.assertIsNone(request.request_envelope._musicbox_startup)

    def test_invalid_bundle_falls_back_to_fresh_response(self):
        request, parsed = handler(), object()
        request.request_envelope._musicbox_startup = payload()['startup']
        with mock.patch.object(player.Api, '_get_json', return_value=({}, None)) as get, \
                mock.patch.object(player, 'from_dict', side_effect=[ValueError('invalid'), parsed]):
            result, error = player.Api.stream_video(request, NEW_SONG)
        self.assertIs(result, parsed)
        self.assertIsNone(error)
        get.assert_called_once()

    def test_peek_does_not_cache_startup_payload(self):
        request = handler()
        with mock.patch.object(player.Api, '_get_json', return_value=(payload(), None)) as get:
            player.Api.get_armed_play(request, peek=True)
        get.assert_called_once_with(request, 'armed_play', {'peek': '1'})
        self.assertIsNone(request.request_envelope._musicbox_startup)

    def test_failed_arm_clears_old_request_payload(self):
        request = handler()
        request.request_envelope._musicbox_startup = payload()['startup']
        with mock.patch.object(player.Api, '_get_json', return_value=(None, Exception('denied'))):
            armed, error = player.Api.get_armed_play(request)
        self.assertIsNone(armed)
        self.assertIsNotNone(error)
        self.assertIsNone(request.request_envelope._musicbox_startup)

    def test_old_server_without_bundle_uses_existing_song_endpoint(self):
        request = handler()
        response = payload()
        del response['startup']
        with mock.patch.object(player.Api, '_get_json', side_effect=[(response, None), ({}, None)]) as get, \
                mock.patch.object(player, 'from_dict', return_value=object()):
            player.Api.get_armed_play(request)
            player.Api.stream_video(request, NEW_SONG)
        self.assertEqual(get.call_count, 2)

class ResumeBundleTests(unittest.TestCase):
    def test_resume_uses_fresh_authorized_arm_stream_without_second_http_request(self):
        request=handler()
        request.request_envelope.context=_node(audio_player=None)
        info={'offset_in_ms':0,'stream_url':'https://old-capability','stream_url_video_id':NEW_SONG}
        metadata=player.player_models.Metadata(title='Song',artist='Artist',video_id=NEW_SONG,thumbnail=None,duration_ms=180000)
        response={'video_id':NEW_SONG,'offset_ms':45000,'kind':'resume',
            'resume_stream':{'audio_url':'https://fresh-authorized-capability'}}
        with mock.patch.object(player.data,'ALEXA_SKILL_NAMESPACE','private-skill'), \
             mock.patch.object(player.Attributes,'get_playback_info',return_value=info), \
             mock.patch.object(player.Attributes,'get_metadata_by_play_order',return_value=metadata), \
             mock.patch.object(player.Api,'_get_json',return_value=(response,None)) as get, \
             mock.patch.object(player.Api,'get_stream') as duplicate, \
             mock.patch.object(player.Controller,'play',return_value='played') as play:
            self.assertEqual(player.Controller.resume(request),'played')
        get.assert_called_once_with(request,'armed_play',{'peek':'1','include_resume_stream':'1'})
        duplicate.assert_not_called()
        self.assertEqual(play.call_args.args[1].stream.audio_url,'https://fresh-authorized-capability')
        self.assertEqual(info['offset_in_ms'],45000)
        self.assertIsNone(request.request_envelope._musicbox_resume_stream)

    def test_failed_authorization_clears_request_local_resume_capability(self):
        request=handler();request.request_envelope._musicbox_resume_stream=(NEW_SONG,'https://stale')
        with mock.patch.object(player.Api,'_get_json',return_value=(None,Exception('denied'))):
            armed,error=player.Api.get_armed_play(request,peek=True,include_resume_stream=True)
        self.assertIsNone(armed);self.assertIsNotNone(error)
        self.assertIsNone(request.request_envelope._musicbox_resume_stream)
