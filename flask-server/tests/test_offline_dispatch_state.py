from unittest import mock
import pytest
from test_next_track import server


@pytest.fixture
def pending():
    with mock.patch.dict(server._now_playing, {
        'video_id': 'AAAAAAAAAAA', 'playing': True, 'playback_processing': True,
        'playback_confirmed': False, 'alexa_intent_at': 42, 'playback_revision': 10,
    }), mock.patch.object(server._playback_output, 'snapshot', return_value={
        'playback_output': 'alexa', 'output_token': 'output1'
    }), mock.patch.object(server, '_computed_position_ms', return_value=1234), \
            mock.patch.object(server, '_notify_sse') as notify:
        yield notify


def test_offline_dispatch_freezes_clock_and_clears_processing(pending):
    assert server._fail_play_dispatch('AAAAAAAAAAA', 42, 'output1', 'Echo offline')
    assert not server._now_playing['playing']
    assert not server._now_playing['playback_processing']
    assert server._now_playing['position_ms'] == 1234
    assert server._now_playing['playback_error']['message'] == 'Echo offline'
    assert server._now_playing['playback_revision'] == 11
    pending.assert_called_once()


@pytest.mark.parametrize('video,intent,token', [
    ('BBBBBBBBBBB', 42, 'output1'), ('AAAAAAAAAAA', 41, 'output1'), ('AAAAAAAAAAA', 42, 'old-output')])
def test_stale_failure_cannot_stop_newer_playback(pending, video, intent, token):
    assert not server._fail_play_dispatch(video, intent, token, 'Old failure')
    assert server._now_playing['playing']
    assert server._now_playing['playback_processing']
    pending.assert_not_called()


def test_old_echo_failure_cannot_pause_phone(pending):
    with mock.patch.object(server._playback_output, 'snapshot', return_value={
        'playback_output': 'phone', 'output_token': 'output1'
    }):
        assert not server._fail_play_dispatch('AAAAAAAAAAA', 42, 'output1', 'Echo offline')
    assert server._now_playing['playing']
    pending.assert_not_called()
