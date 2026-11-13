"""A direct play needs one arm response, not an additional song HTTP request."""
from unittest import mock
from test_next_track import NextTrackBase, server, _meta


class StartupBundleTests(NextTrackBase):
    def setUp(self):
        super().setUp()
        self.vid = 'AAAAAAAAAAA'
        server._now_playing.update(video_id=self.vid, queue=[_meta(self.vid)], queue_index=0)
        server._arm_play(self.SERIAL, self.vid, 1234)
        self.url = '/armed_play/?key='+server.API_KEY
        self.patch = mock.patch.object(server.Supporting, 'get_stream',
            new=mock.AsyncMock(return_value={'audio_url': 'https://musicbox.example/audio'}))
        self.stream = self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_bundle_contains_normalized_song_and_only_consumes_once(self):
        result = self.client.get(self.url+'&include_stream=1').json
        assert result['video_id'] == self.vid
        assert result['offset_ms'] == 1234
        assert result['startup']['song_info']['stream']['audio_url'].endswith('/audio')
        assert result['startup']['song_info']['metadata']['thumbnail'] is None
        assert result['startup']['playlist'][0]['video_id'] == self.vid
        assert self.client.get(self.url+'&include_stream=1').json['video_id'] is None
        self.stream.assert_awaited_once_with(self.vid)

    def test_legacy_client_keeps_original_response(self):
        result = self.client.get(self.url).json
        assert result['video_id'] == self.vid
        assert 'startup' not in result
        self.stream.assert_not_awaited()

    def test_peek_does_not_consume_or_prepare_a_stream(self):
        result = self.client.get(self.url+'&peek=1&include_stream=1').json
        assert result['video_id'] == self.vid
        assert 'startup' not in result
        assert server._consume_armed_play(self.SERIAL)[0] == self.vid
        self.stream.assert_not_awaited()

    def test_queue_sync_keeps_its_control_path(self):
        server._arm_play(self.SERIAL, self.vid, kind='queue_sync:token')
        result = self.client.get(self.url+'&include_stream=1').json
        assert 'startup' not in result
        self.stream.assert_not_awaited()

    def test_superseded_current_song_does_not_bundle_the_wrong_queue(self):
        server._now_playing['video_id'] = 'BBBBBBBBBBB'
        result = self.client.get(self.url+'&include_stream=1').json
        assert 'startup' not in result
        self.stream.assert_not_awaited()

    def test_large_queue_is_windowed_around_selected_duplicate(self):
        queue = [_meta(f'{i:011d}') for i in range(250)]
        queue[0] = _meta(self.vid, 'Earlier occurrence')
        queue[180] = _meta(self.vid, 'Selected occurrence')
        server._now_playing.update(queue=queue, queue_index=180)
        result = self.client.get(self.url+'&include_stream=1').json['startup']
        assert len(result['playlist']) == 95
        assert len(result['playlist']) <= server._SKILL_QUEUE_WINDOW
        assert result['playlist'][server._SKILL_QUEUE_BEHIND]['title'] == 'Selected occurrence'
        assert result['song_info']['metadata']['title'] == 'Selected occurrence'
        assert result['queue_index'] == server._SKILL_QUEUE_BEHIND
