"""Account failures stop the exact attempt without consuming its music queue."""
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest import mock
import time
import pytest
from test_next_track import server
from multiuser.alexa_access import AccessDenied
from alexa_playback_errors import authentication_failure


@pytest.fixture
def pending():
    timer = mock.Mock()
    with mock.patch.dict(server._now_playing, {
        'video_id':'AAAAAAAAAAA','queue':[{'video_id':'AAAAAAAAAAA'},{'video_id':'BBBBBBBBBBB'}],
        'queue_index':0, 'playing':True, 'playback_processing':True, 'playback_confirmed':False,
        'alexa_intent_at':time.time()-2, 'playback_revision':10, '_alexa_auth_blocked':False,
    }), mock.patch.object(server._playback_output,'snapshot',return_value={
        'playback_output':'alexa','output_token':'output1'}), \
            mock.patch.object(server,'_computed_position_ms',return_value=1234), \
            mock.patch.object(server,'_notify_sse'), \
            mock.patch.object(server,'_PENDING_DISPATCH',{'echo':{'timer':timer}}), \
            mock.patch.object(server,'_ARMED_PLAYS',{'echo':{'video_id':'AAAAAAAAAAA'}}), \
            mock.patch.object(server,'_TRIGGER_INFLIGHT',{'echo':time.time()}):
        yield timer


@pytest.mark.parametrize('code', ['amazon_email_mismatch','musicbox_email_mismatch',
    'amazon_permission_required','amazon_link_required','amazon_link_changed','amazon_profile_unavailable'])
def test_authentication_failure_is_terminal_and_preserves_selection(pending, code):
    assert server._stop_alexa_auth_failure('',code)
    state=server._now_playing
    assert state['video_id']=='AAAAAAAAAAA' and state['queue_index']==0 and len(state['queue'])==2
    assert not state['playing'] and not state['playback_processing'] and not state['playback_confirmed']
    assert state['playback_error']['terminal'] and not state['playback_error']['retryable']
    assert state['playback_error']['code']==code
    assert state['_alexa_auth_blocked'] and state['position_ms']==1234
    assert not server._ARMED_PLAYS and not server._TRIGGER_INFLIGHT and not server._PENDING_DISPATCH
    pending.cancel.assert_called_once()


def test_no_retry_or_music_failover_after_error_was_shown(pending):
    server._stop_alexa_auth_failure('', 'amazon_email_mismatch')
    server._now_playing['playback_error']=None  # the UI consumed its one-shot event
    assert server._schedule_play_dispatch('echo','AAAAAAAAAAA') is None
    with mock.patch.object(server,'_queue_neighbor') as neighbor, \
            mock.patch.object(server,'_arm_play') as arm, \
            mock.patch.object(server.alexa_remote.remote,'play_video_id',create=True) as play:
        assert server._auto_advance_after_failure('echo','AAAAAAAAAAA','unavailable')
        assert server._dispatch_play_with_retry('echo','AAAAAAAAAAA')
    neighbor.assert_not_called(); arm.assert_not_called(); play.assert_not_called()


def test_authentication_failure_during_song_selection_cannot_commit_fallback(pending):
    def neighbor(*args):
        server._stop_alexa_auth_failure('', 'amazon_email_mismatch')
        return (1, {'video_id':'BBBBBBBBBBB','title':'Next'}), None
    with mock.patch.object(server,'_queue_neighbor',side_effect=neighbor), \
            mock.patch.object(server,'_dispatch_play_with_retry') as send:
        assert server._auto_advance_after_failure('echo','AAAAAAAAAAA','unavailable')
    assert server._now_playing['video_id']=='AAAAAAAAAAA'
    send.assert_not_called()


def test_stale_auth_failure_cannot_stop_new_song_or_phone(pending):
    assert not server._stop_alexa_auth_failure('', 'amazon_email_mismatch', expected_video='BBBBBBBBBBB')
    assert not server._stop_alexa_auth_failure('', 'amazon_email_mismatch', expected_output='retired')
    with mock.patch.object(server._playback_output,'snapshot',return_value={'playback_output':'phone','output_token':'output1'}):
        assert not server._stop_alexa_auth_failure('', 'amazon_email_mismatch')
    assert server._now_playing['playing']


def test_recent_denial_stops_only_the_authenticated_accounts_pending_intent(pending):
    with server.app.test_request_context('/',headers={
        'X-Alexa-Request-Timestamp':datetime.now(timezone.utc).isoformat(), 'X-Alexa-Request-Type':'IntentRequest'}), \
            mock.patch.object(server,'_player_identity_for_user_id',return_value=server._current_identity()):
        assert server._report_alexa_auth_denial('user-a','amazon_email_mismatch')
    assert server._now_playing['_alexa_auth_blocked']


def test_old_callback_denial_cannot_stop_new_attempt(pending):
    for timestamp, kind, token in [
        (datetime.fromtimestamp(time.time()-30,timezone.utc).isoformat(),'IntentRequest',''),
        (datetime.now(timezone.utc).isoformat(),'AudioPlayer.PlaybackStopped','0|BBBBBBBBBBB|old')]:
        with server.app.test_request_context('/',headers={'X-Alexa-Request-Timestamp':timestamp,
            'X-Alexa-Request-Type':kind,'X-Alexa-Playback-Token':token}), \
                mock.patch.object(server,'_player_identity_for_user_id',return_value=server._current_identity()):
            assert not server._report_alexa_auth_denial('user-a','amazon_email_mismatch')
    assert server._now_playing['playing']


def test_revoked_last_used_token_stops_before_retry_or_unavailable_song_skip(pending):
    server._now_playing['_alexa_verified_token_id']='old-token'
    layer=SimpleNamespace(alexa_access=SimpleNamespace(current=mock.Mock(side_effect=AccessDenied())))
    with mock.patch.object(server,'_multiuser_layer',layer), \
            mock.patch.object(server,'PLAYBACK_CONFIRM_TIMEOUT',0), \
            mock.patch.object(server,'PLAYBACK_CONFIRM_TIMEOUT_CACHED',0), \
            mock.patch.object(server,'_auto_advance_after_failure') as advance, \
            mock.patch.object(server,'_is_dead_or_flaky_video',return_value=True):
        resend=mock.Mock()
        server._watch_playback_confirmation('echo','AAAAAAAAAAA',resend)
    resend.assert_not_called(); advance.assert_not_called()
    assert server._now_playing['playback_error']['code']=='alexa_access_denied'


def test_offline_and_unavailable_are_not_misreported_as_authentication():
    assert authentication_failure('Echo is offline') is None
    assert authentication_failure('YouTube authentication failed') is None
    assert authentication_failure('This song is unavailable') is None
    assert authentication_failure('', 'unavailable') is None
    assert authentication_failure({'unexpected':'json'}, ['invalid']) is None



def test_manual_verified_resume_releases_block_and_preserves_failed_selection(pending):
    server._stop_alexa_auth_failure('', 'amazon_email_mismatch')
    with server.app.test_request_context('/', headers={
        'X-Alexa-Request-Timestamp':datetime.now(timezone.utc).isoformat(),
        'X-Alexa-Request-Type':'IntentRequest', 'X-Alexa-Intent-Name':'AMAZON.ResumeIntent'}):
        assert server._accept_verified_alexa_retry()
    assert not server._now_playing['_alexa_auth_blocked']
    assert server._now_playing['video_id']=='AAAAAAAAAAA'
    armed=server._peek_armed_play()
    assert armed[0]=='AAAAAAAAAAA' and armed[1]==1234 and armed[3]=='resume'


def test_background_callback_cannot_release_authentication_block(pending):
    server._stop_alexa_auth_failure('', 'amazon_email_mismatch')
    with server.app.test_request_context('/', headers={
        'X-Alexa-Request-Timestamp':datetime.now(timezone.utc).isoformat(),
        'X-Alexa-Request-Type':'AudioPlayer.PlaybackStarted'}):
        assert not server._accept_verified_alexa_retry()
    assert server._now_playing['_alexa_auth_blocked']


def test_initial_dispatch_login_error_stops_without_starting_a_watchdog(pending):
    with mock.patch.object(server.alexa_remote.remote,'play_video_id',create=True,
            return_value='Not signed in. Sign in to Amazon.') as send, \
            mock.patch.object(server,'_spawn_for_identity') as spawn:
        assert server._dispatch_play_with_retry('echo','AAAAAAAAAAA')
    send.assert_called_once()
    spawn.assert_not_called()
    assert server._now_playing['_alexa_auth_blocked']
    assert not server._now_playing['playing']


def test_resume_login_error_does_not_restore_previous_playing_snapshot(pending):
    with mock.patch.object(server.alexa_remote.remote,'command',create=True,
            return_value='Not signed in. Sign in to Amazon.') as command, \
            mock.patch.object(server,'_ensure_audio_ready_for_play'):
        response=server.app.test_client().post('/alexa/command/?key='+server.API_KEY,
            json={'serial':'echo','action':'play'})
    assert response.status_code==403
    assert response.json['error']['code']=='alexa_authentication_required'
    assert 'Reconnect Amazon' in response.json['error']['message']
    assert server._now_playing['_alexa_auth_blocked']
    assert not server._now_playing['playing']
    assert not server._now_playing['playback_processing']
    command.assert_called_once()



def test_no_authenticated_arm_acknowledgement_means_no_resend_or_song_fallback(pending):
    server._now_playing.pop('_alexa_verified_token_id',None)
    server._now_playing.pop('_alexa_verified_intent_at',None)
    with mock.patch.object(server,'_multiuser_layer',SimpleNamespace()),             mock.patch.object(server,'PLAYBACK_CONFIRM_TIMEOUT',0),             mock.patch.object(server,'PLAYBACK_CONFIRM_TIMEOUT_CACHED',0),             mock.patch.object(server,'_auto_advance_after_failure') as advance,             mock.patch.object(server,'_is_dead_or_flaky_video',return_value=True):
        resend=mock.Mock()
        server._watch_playback_confirmation('echo','AAAAAAAAAAA',resend)
    resend.assert_not_called(); advance.assert_not_called()
    assert server._now_playing['playback_error']['code']=='alexa_request_unverified'
    assert server._now_playing['video_id']=='AAAAAAAAAAA'



def test_url_preparation_cannot_prolong_an_unverified_play_attempt(pending):
    server._now_playing.pop('_alexa_verified_token_id',None)
    server._now_playing.pop('_alexa_verified_intent_at',None)
    clock=[100.0]
    downloads=mock.Mock(side_effect=[True]*20+[False]*20)
    def sleep(seconds):clock[0]+=0.01
    with mock.patch.object(server,'_multiuser_layer',SimpleNamespace()),             mock.patch.object(server,'PLAYBACK_CONFIRM_TIMEOUT',0.03),             mock.patch.object(server,'PLAYBACK_CONFIRM_TIMEOUT_CACHED',0.03),             mock.patch.object(server,'_download_in_progress',downloads),             mock.patch.object(server.time,'time',side_effect=lambda:clock[0]),             mock.patch.object(server.time,'sleep',side_effect=sleep):
        resend=mock.Mock()
        server._watch_playback_confirmation('echo','AAAAAAAAAAA',resend)
    assert clock[0]<100.1
    assert downloads.call_count<10
    resend.assert_not_called()
    assert server._now_playing['playback_error']['code']=='alexa_request_unverified'



def test_auth_denial_for_buffered_successor_stops_the_proven_current_stream(pending):
    server._now_playing.update(alexa_token='0|AAAAAAAAAAA|current', playback_processing=False)
    with server.app.test_request_context('/',headers={
        'X-Alexa-Request-Timestamp':datetime.now(timezone.utc).isoformat(),
        'X-Alexa-Request-Type':'AudioPlayer.PlaybackFailed',
        'X-Alexa-Playback-Token':'1|BBBBBBBBBBB|buffered',
        'X-Alexa-Current-Playback-Token':'0|AAAAAAAAAAA|current'}),             mock.patch.object(server,'_player_identity_for_user_id',return_value=server._current_identity()):
        assert server._report_alexa_auth_denial('user-a','amazon_email_mismatch')
    assert server._now_playing['_alexa_auth_blocked']
    assert server._now_playing['video_id']=='AAAAAAAAAAA'
