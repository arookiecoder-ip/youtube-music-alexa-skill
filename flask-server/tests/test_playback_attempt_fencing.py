"""Old startup workers must not undo newer user intent."""
from unittest import mock
import pytest
from test_next_track import server


@pytest.mark.parametrize('change', ['pause', 'replay', 'output', 'track'])
def test_superseded_start_does_not_retry_or_mutate_state(change):
    with mock.patch.dict(server._now_playing, {
        'video_id': 'AAAAAAAAAAA', 'playing': True, 'playback_confirmed': False,
        'alexa_intent_at': 42, 'failed_playback_revision': 0,
    }), mock.patch.object(server._playback_output, 'snapshot', return_value={
        'playback_output': 'alexa', 'output_token': 'output1'
    }) as output, mock.patch.object(server, '_update_now_playing') as update, \
            mock.patch.object(server, '_auto_advance_after_failure') as advance, \
            mock.patch.object(server._youtube_stream_for().current, 'has', return_value=False):
        if change == 'pause':
            server._now_playing.update(playing=False, alexa_intent_at=43)
        elif change == 'replay':
            server._now_playing['alexa_intent_at'] = 43
        elif change == 'output':
            output.return_value = {'playback_output': 'phone', 'output_token': 'output2'}
        else:
            server._now_playing['video_id'] = 'BBBBBBBBBBB'
        resend = mock.Mock()
        server._watch_playback_confirmation('device', 'AAAAAAAAAAA', resend, (42, 'output1'))
        resend.assert_not_called()
        update.assert_not_called()
        advance.assert_not_called()


def test_network_send_cannot_rebind_watchdog_to_newer_same_song_attempt():
    with mock.patch.dict(server._now_playing, {'alexa_intent_at': 42}), \
            mock.patch.object(server._playback_output, 'snapshot', return_value={'output_token': 'first'}), \
            mock.patch.object(server, '_arm_play'), \
            mock.patch.object(server, '_spawn_for_identity') as spawn, \
            mock.patch.object(server.alexa_remote.remote, 'play_video_id', create=True) as send:
        def newer_intent(*args):
            server._now_playing['alexa_intent_at'] = 43
        send.side_effect = newer_intent
        server._dispatch_play_with_retry('device', 'AAAAAAAAAAA')
        assert spawn.call_args.kwargs['args'][-1] == (42, 'first')


def test_two_users_scheduling_same_serial_keep_independent_dispatches():
    timers, sent = [], []

    def timer_factory(delay, callback):
        timer = mock.Mock()
        timer.fire = callback
        timers.append(timer)
        return timer

    original = server._PLAYER_IDENTITY.set('user:dispatch-test-a')
    try:
        with mock.patch.object(server.threading, 'Timer', side_effect=timer_factory), \
                mock.patch.object(server, '_arm_play'), \
                mock.patch.object(server, '_note_trigger_sent'), \
                mock.patch.object(server, '_trigger_in_flight', return_value=False), \
                mock.patch.object(server, '_dispatch_play_with_retry',
                    side_effect=lambda s, v, o: sent.append((server._current_identity(), v))):
            server._schedule_play_dispatch('same-serial', 'AAAAAAAAAAA')
            server._PLAYER_IDENTITY.set('user:dispatch-test-b')
            server._schedule_play_dispatch('same-serial', 'BBBBBBBBBBB')
            for timer in timers:
                timer.fire()
        assert sent == [('user:dispatch-test-a', 'AAAAAAAAAAA'),
                        ('user:dispatch-test-b', 'BBBBBBBBBBB')]
    finally:
        server._PLAYER_IDENTITY.reset(original)


def test_skill_consumes_only_authenticated_accounts_arm_without_serial():
    original = server._PLAYER_IDENTITY.set('user:arm-test-a')
    try:
        server._arm_play('echo-a', 'AAAAAAAAAAA')
        server._note_trigger_sent('echo-a')
        assert server._claim_play_intent('shared', 20)
        server._PLAYER_IDENTITY.set('user:arm-test-b')
        assert server._peek_armed_play() is None
        assert server._consume_armed_play() is None
        assert not server._trigger_in_flight('echo-a')
        assert server._claim_play_intent('shared', 1)
        server._arm_play('echo-b', 'BBBBBBBBBBB')
        assert server._consume_armed_play()[0] == 'BBBBBBBBBBB'
        server._PLAYER_IDENTITY.set('user:arm-test-a')
        assert server._consume_armed_play()[0] == 'AAAAAAAAAAA'
        assert server._trigger_in_flight('echo-a')
        server._clear_trigger_inflight()
    finally:
        server._PLAYER_IDENTITY.reset(original)
