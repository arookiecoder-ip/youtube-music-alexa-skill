"""Unit tests for resume self-correction (resume restarts the wrong song).

Bug (reported): song A playing, user asks Alexa to play song B (song B is
heard on the Echo), then pauses via the web remote's YouTube Music icon, then
resumes from the web remote -- and song A plays instead of song B.

Root cause: a web resume is a bare "resume" transport command, so the skill
restores whatever its persisted AudioPlayer session remembers. That memory can
disagree with the live server state (a voice play that landed after the web
client's last sync, a lost persistence save, or voice and web driving two
Amazon accounts with separate persistence). Resuming the stale session then
restarts the *older* song.

Fix: the web remote arms the exact track+offset it shows on every resume
(``kind='resume'``). ``Controller.resume`` peeks at that arm and, when it
names a *different* track than the persisted session (and is fresh), plays
the armed track at the armed offset instead. Peeking never consumes, so an
in-flight fresh play/seek keeps its own arm; only fresh 'resume' arms are
ever honored.

Also covered: ``Controller.record_stop`` ignores a stale PlaybackStopped for
an already-interrupted track (it must not overwrite the new track's offset),
while a genuine stop still records its offset and clears the enqueue flag.

These tests import the real player.py with the ask-sdk / dacite / urllib3
modules stubbed out, following test_enqueue_next_stream.py.
"""
import os
import sys
import types
import unittest
from unittest import mock

_LAMBDA_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, _LAMBDA_DIR)


class _Kwargs:
    """Minimal stand-in for ask-sdk model objects: stores constructor kwargs."""

    def __init__(self, *args, **kwargs):
        self.__dict__.update(kwargs)
        if args:
            self.args = args


def _install_stubs():
    if 'ask_sdk_model' not in sys.modules:
        ask_sdk_model = types.ModuleType('ask_sdk_model')
        ask_sdk_model.Response = _Kwargs
        sys.modules['ask_sdk_model'] = ask_sdk_model

        interfaces = types.ModuleType('ask_sdk_model.interfaces')
        sys.modules['ask_sdk_model.interfaces'] = interfaces

        audioplayer = types.ModuleType('ask_sdk_model.interfaces.audioplayer')
        audioplayer.PlayDirective = _Kwargs
        audioplayer.PlayBehavior = types.SimpleNamespace(
            ENQUEUE='ENQUEUE', REPLACE_ALL='REPLACE_ALL')
        audioplayer.AudioItem = _Kwargs
        audioplayer.Stream = _Kwargs
        audioplayer.AudioItemMetadata = _Kwargs
        audioplayer.StopDirective = _Kwargs
        interfaces.audioplayer = audioplayer
        sys.modules['ask_sdk_model.interfaces.audioplayer'] = audioplayer

        display_mod = types.ModuleType('ask_sdk_model.interfaces.display')
        display_mod.Image = _Kwargs
        display_mod.ImageInstance = _Kwargs
        interfaces.display = display_mod
        sys.modules['ask_sdk_model.interfaces.display'] = display_mod

        services = types.ModuleType('ask_sdk_model.services')
        sys.modules['ask_sdk_model.services'] = services
        directive = types.ModuleType('ask_sdk_model.services.directive')
        directive.SendDirectiveRequest = _Kwargs
        directive.Header = _Kwargs
        directive.SpeakDirective = _Kwargs
        services.directive = directive
        sys.modules['ask_sdk_model.services.directive'] = directive

    if 'ask_sdk_core' not in sys.modules:
        ask_sdk_core = types.ModuleType('ask_sdk_core')
        sys.modules['ask_sdk_core'] = ask_sdk_core
        handler_input_mod = types.ModuleType('ask_sdk_core.handler_input')
        handler_input_mod.HandlerInput = _Kwargs
        sys.modules['ask_sdk_core.handler_input'] = handler_input_mod

    if 'dacite' not in sys.modules:
        dacite = types.ModuleType('dacite')
        dacite.from_dict = lambda klass, data: klass(**data)
        sys.modules['dacite'] = dacite

    if 'urllib3' not in sys.modules:
        urllib3 = types.ModuleType('urllib3')
        urllib3.PoolManager = lambda *a, **k: None
        sys.modules['urllib3'] = urllib3


_install_stubs()

from mediaUtils import player  # noqa: E402
from models import player_models  # noqa: E402
import data  # noqa: E402


OLD_SONG = 'OLDSONG00001'
NEW_SONG = 'NEWSONG00001'


def _node(**kwargs):
    obj = types.SimpleNamespace()
    for key, value in kwargs.items():
        setattr(obj, key, value)
    return obj


def _raw(video_id, title=None):
    return {
        'title': title or ('Song ' + video_id),
        'artist': 'Test Artist',
        'video_id': video_id,
        'thumbnail': None,
        'duration_ms': 180000,
    }


def _make_handler_input(user_attr):
    response_builder = _node()
    response_builder.directives = []

    def _add(directive):
        response_builder.directives.append(directive)
        return response_builder

    response_builder.add_directive = _add
    response_builder.set_should_end_session = lambda value: response_builder
    response_builder.speak = lambda speech: response_builder
    response_builder.response = response_builder
    attributes_manager = _node()
    attributes_manager.persistent_attributes = {'test-user': user_attr}
    request_envelope = _node(
        context=_node(system=_node(user=_node(user_id='test-user'))))
    return _node(response_builder=response_builder,
                 attributes_manager=attributes_manager,
                 request_envelope=request_envelope)


def _user_attr(playlist_raw, index, play_order, offset_ms=0, enqueued=False,
               stream_url=None, stream_url_video_id=None):
    return {
        'playback_info': {
            'index': index,
            'offset_in_ms': offset_ms,
            'play_order': list(play_order),
            'next_stream_enqueued': enqueued,
            'stream_url': stream_url,
            'stream_url_video_id': stream_url_video_id,
            'current_token': None,
        },
        'playlist': list(playlist_raw),
        'playback_setting': {'loop': False, 'shuffle': False},
    }


def _played_stream(hi):
    directives = hi.response_builder.directives
    if not directives:
        return None
    return directives[0].audio_item.stream


def _armed(video_id, offset_ms=45000, age_s=2.0, kind='resume'):
    return (video_id, offset_ms, age_s, kind), None


def _no_arm():
    return None, Exception(data.NOT_FOUND)


class ResumeHonorsFreshResumeArm(unittest.TestCase):
    """The reported bug: persisted session says OLD, the web remote armed NEW."""

    def test_diverging_arm_plays_armed_track_at_armed_offset(self):
        hi = _make_handler_input(_user_attr(
            [_raw(OLD_SONG)], index=0, play_order=[0], offset_ms=120000))
        song_info = _node(
            metadata=player_models.Metadata(
                title='Song ' + NEW_SONG, artist='Test Artist',
                video_id=NEW_SONG, thumbnail=None, duration_ms=240000),
            stream=_node(audio_url='http://stream-new'))
        song_info_list = _node(playlist=[song_info.metadata],
                               song_info=song_info)
        with mock.patch.object(player.Api, 'get_armed_play',
                               return_value=_armed(NEW_SONG)) as arm_mock, \
             mock.patch.object(player.Api, 'stream_video',
                               return_value=(song_info_list, None)) as stream_mock, \
             mock.patch.object(player.Api, 'get_stream') as get_stream_mock:
            player.Controller.resume(hi, is_playback=True)
        # The peek must not consume: fresh plays/seeks keep their own arm.
        arm_mock.assert_called_once()
        _, kwargs = arm_mock.call_args
        self.assertTrue(kwargs.get('peek'),
                        msg="resume must peek, never consume, the arm")
        stream_mock.assert_called_once()
        get_stream_mock.assert_not_called()
        stream = _played_stream(hi)
        self.assertIsNotNone(stream)
        self.assertIn(NEW_SONG, stream.token)
        self.assertEqual(stream.url, 'http://stream-new')
        self.assertEqual(stream.offset_in_milliseconds, 45000)

    def test_matching_arm_keeps_normal_session_resume(self):
        """Arm agrees with persistence: restore the session as before (keeps
        the rapid pause/resume behavior unchanged)."""
        hi = _make_handler_input(_user_attr(
            [_raw(NEW_SONG)], index=0, play_order=[0], offset_ms=45000,
            stream_url='http://stream-new', stream_url_video_id=NEW_SONG))
        with mock.patch.object(player.Api, 'get_armed_play',
                               return_value=_armed(NEW_SONG)), \
             mock.patch.object(player.Api, 'stream_video') as stream_mock, \
             mock.patch.object(player.Api, 'get_stream') as get_stream_mock:
            player.Controller.resume(hi, is_playback=True)
        # Same track: no explicit re-fetch, session restore path.
        stream_mock.assert_not_called()
        get_stream_mock.assert_not_called()
        stream = _played_stream(hi)
        self.assertIsNotNone(stream)
        self.assertIn(NEW_SONG, stream.token)
        self.assertEqual(stream.url, 'http://stream-new')

    def test_no_arm_keeps_normal_session_resume(self):
        """Voice resumes (and old servers) arm nothing: behavior unchanged."""
        hi = _make_handler_input(_user_attr(
            [_raw(OLD_SONG)], index=0, play_order=[0], offset_ms=120000))
        with mock.patch.object(player.Api, 'get_armed_play',
                               return_value=_no_arm()), \
             mock.patch.object(player.Api, 'get_stream',
                               return_value=(_node(audio_url='http://stream-old'),
                                             None)):
            player.Controller.resume(hi, is_playback=True)
        stream = _played_stream(hi)
        self.assertIsNotNone(stream)
        self.assertIn(OLD_SONG, stream.token)

    def test_stale_arm_is_ignored(self):
        """An arm older than the window must never hijack a resume."""
        hi = _make_handler_input(_user_attr(
            [_raw(OLD_SONG)], index=0, play_order=[0], offset_ms=120000))
        with mock.patch.object(player.Api, 'get_armed_play',
                               return_value=_armed(NEW_SONG, age_s=3600.0)), \
             mock.patch.object(player.Api, 'stream_video') as stream_mock, \
             mock.patch.object(player.Api, 'get_stream',
                               return_value=(_node(audio_url='http://stream-old'),
                                             None)):
            player.Controller.resume(hi, is_playback=True)
        stream_mock.assert_not_called()
        self.assertIn(OLD_SONG, _played_stream(hi).token)

    def test_fresh_play_arm_is_ignored_by_resume(self):
        """Only kind='resume' arms redirect a resume: a peeked fresh-play arm
        (e.g. its trigger still in flight) must not divert it."""
        hi = _make_handler_input(_user_attr(
            [_raw(OLD_SONG)], index=0, play_order=[0], offset_ms=120000))
        with mock.patch.object(player.Api, 'get_armed_play',
                               return_value=_armed(NEW_SONG, kind='play')), \
             mock.patch.object(player.Api, 'stream_video') as stream_mock, \
             mock.patch.object(player.Api, 'get_stream',
                               return_value=(_node(audio_url='http://stream-old'),
                                             None)):
            player.Controller.resume(hi, is_playback=True)
        stream_mock.assert_not_called()
        self.assertIn(OLD_SONG, _played_stream(hi).token)

    def test_empty_session_with_arm_plays_armed_track(self):
        """No persisted playlist at all (fresh install, lost save) plus a
        resume arm: play the armed track instead of erroring."""
        hi = _make_handler_input(_user_attr([], index=0, play_order=[]))
        song_info = _node(
            metadata=player_models.Metadata(
                title='Song ' + NEW_SONG, artist='Test Artist',
                video_id=NEW_SONG, thumbnail=None, duration_ms=240000),
            stream=_node(audio_url='http://stream-new'))
        song_info_list = _node(playlist=[song_info.metadata],
                               song_info=song_info)
        with mock.patch.object(player.Api, 'get_armed_play',
                               return_value=_armed(NEW_SONG)), \
             mock.patch.object(player.Api, 'stream_video',
                               return_value=(song_info_list, None)):
            player.Controller.resume(hi, is_playback=True)
        stream = _played_stream(hi)
        self.assertIsNotNone(stream)
        self.assertIn(NEW_SONG, stream.token)

    def test_empty_session_without_arm_still_errors(self):
        hi = _make_handler_input(_user_attr([], index=0, play_order=[]))
        with mock.patch.object(player.Api, 'get_armed_play',
                               return_value=_no_arm()):
            player.Controller.resume(hi, is_playback=True)
        self.assertEqual(hi.response_builder.directives, [])


class RecordStopIgnoresStaleStops(unittest.TestCase):
    def test_stale_stop_keeps_current_offset_and_flag(self):
        """Persisted session moved on to NEW; the late stop for OLD changes
        nothing."""
        hi = _make_handler_input(_user_attr(
            [_raw(NEW_SONG)], index=0, play_order=[0], offset_ms=5000,
            enqueued=True))
        stale = player.Controller.record_stop(hi, OLD_SONG, 120000)
        self.assertTrue(stale)
        info = hi.attributes_manager.persistent_attributes['test-user']['playback_info']
        self.assertEqual(info['offset_in_ms'], 5000)
        self.assertTrue(info['next_stream_enqueued'])

    def test_genuine_stop_records_offset_and_clears_flag(self):
        hi = _make_handler_input(_user_attr(
            [_raw(NEW_SONG)], index=0, play_order=[0], offset_ms=5000,
            enqueued=True))
        stale = player.Controller.record_stop(hi, NEW_SONG, 45000)
        self.assertFalse(stale)
        info = hi.attributes_manager.persistent_attributes['test-user']['playback_info']
        self.assertEqual(info['offset_in_ms'], 45000)
        self.assertFalse(info['next_stream_enqueued'])

    def test_stop_without_token_records_offset(self):
        """Legacy callers with no token keep the old always-record behavior."""
        hi = _make_handler_input(_user_attr(
            [_raw(NEW_SONG)], index=0, play_order=[0], offset_ms=5000))
        stale = player.Controller.record_stop(hi, '', 45000)
        self.assertFalse(stale)
        info = hi.attributes_manager.persistent_attributes['test-user']['playback_info']
        self.assertEqual(info['offset_in_ms'], 45000)


if __name__ == '__main__':
    unittest.main()
