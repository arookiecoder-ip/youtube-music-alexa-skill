"""Standalone unit tests for the skill's next-track enqueue logic.

Covers the split-brain fix:
  - ``Controller.enqueue_next_stream`` now resolves the next track from the
    server's authoritative queue (Api.next_track) instead of trusting its own
    stale window, falling back to the window wrap / extension behavior when the
    server has no next.
  - ``Controller._stage_next_track`` keeps the skill's window + index
    bookkeeping consistent with that authoritative choice.

These tests import the real player.py with the ask-sdk / dacite / urllib3
modules stubbed out, and drive it with a fake HandlerInput whose persistent
attributes behave like the DynamoDB state. Only ``Api`` and
``Controller.extend_queue`` are mocked; the Attributes layer and the staging /
enqueue control flow are the real code under test.
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


def _raw(video_id, title=None):
    return {
        'title': title or ('Song ' + video_id),
        'artist': 'Test Artist',
        'video_id': video_id,
        'thumbnail': None,
        'duration_ms': 180000,
    }


def _meta(video_id, title=None):
    return player_models.Metadata(
        title=title or ('Song ' + video_id),
        artist='Test Artist',
        video_id=video_id,
        thumbnail=None,
        duration_ms=180000,
    )


def _node(**kwargs):
    obj = types.SimpleNamespace()
    for key, value in kwargs.items():
        setattr(obj, key, value)
    return obj


def _make_handler_input(user_attr):
    response_builder = _node()
    response_builder.directives = []
    response_builder.add_directive = response_builder.directives.append
    attributes_manager = _node()
    attributes_manager.persistent_attributes = {'test-user': user_attr}
    request_envelope = _node(
        context=_node(system=_node(user=_node(user_id='test-user'))))
    return _node(response_builder=response_builder,
                 attributes_manager=attributes_manager,
                 request_envelope=request_envelope)


def _user_attr(playlist_raw, index, play_order, loop=False, enqueued=False,
               current_token=None):
    return {
        'playback_info': {
            'index': index,
            'play_order': list(play_order),
            'next_stream_enqueued': enqueued,
            'current_token': current_token,
        },
        'playlist': list(playlist_raw),
        'playback_setting': {'loop': loop, 'shuffle': False},
    }


class StageNextTrackTests(unittest.TestCase):
    """_stage_next_track keeps play_order/index consistent with the server's
    authoritative next choice."""

    def _stage(self, user_attr, next_meta, current_index):
        hi = _make_handler_input(user_attr)
        return player.Controller._stage_next_track(hi, next_meta, current_index), hi

    def test_noop_when_next_already_in_slot(self):
        user_attr = _user_attr([_raw('A'), _raw('B'), _raw('C')],
                               index=0, play_order=[0, 1, 2])
        result, hi = self._stage(user_attr, _meta('B'), 0)
        self.assertEqual(result, 1)
        self.assertEqual(hi.attributes_manager.persistent_attributes['test-user']
                         ['playback_info']['play_order'], [0, 1, 2])

    def test_moves_existing_track_to_next(self):
        user_attr = _user_attr([_raw('A'), _raw('B'), _raw('C')],
                               index=0, play_order=[0, 1, 2])
        result, hi = self._stage(user_attr, _meta('C'), 0)
        self.assertEqual(result, 1)
        info = hi.attributes_manager.persistent_attributes['test-user']['playback_info']
        self.assertEqual(info['play_order'], [0, 2, 1])
        self.assertEqual(info['index'], 0)  # current track stays put

    def test_pulling_already_played_track_adjusts_index(self):
        # B is playing (index 1); the user re-queued A to play next. Removing
        # A's pointer from before B shifts B's slot down to 0.
        user_attr = _user_attr([_raw('A'), _raw('B'), _raw('C')],
                               index=1, play_order=[0, 1, 2])
        result, hi = self._stage(user_attr, _meta('A'), 1)
        self.assertEqual(result, 1)
        info = hi.attributes_manager.persistent_attributes['test-user']['playback_info']
        self.assertEqual(info['play_order'], [1, 0, 2])
        self.assertEqual(info['index'], 0)  # current (B) moved to slot 0

    def test_appends_new_track_not_in_window(self):
        user_attr = _user_attr([_raw('A'), _raw('B')],
                               index=0, play_order=[0, 1])
        result, hi = self._stage(user_attr, _meta('Z'), 0)
        self.assertEqual(result, 1)
        attrs = hi.attributes_manager.persistent_attributes['test-user']
        self.assertEqual([m['video_id'] for m in attrs['playlist']],
                         ['A', 'B', 'Z'])
        self.assertEqual(attrs['playback_info']['play_order'], [0, 2, 1])

    def test_internal_error_returns_none(self):
        user_attr = _user_attr([_raw('A'), _raw('B')],
                               index=0, play_order=[0, 1])
        hi = _make_handler_input(user_attr)
        with mock.patch.object(player.Attributes, 'get_playlist',
                               side_effect=RuntimeError('boom')):
            self.assertIsNone(player.Controller._stage_next_track(hi, _meta('B'), 0))


class EnqueueNextStreamTests(unittest.TestCase):
    """enqueue_next_stream resolves the next track from the server's live queue
    and falls back to the window when the server has no next."""

    def _hi(self, playlist_raw, index, play_order, loop=False, enqueued=False,
            current_token=None):
        user_attr = _user_attr(playlist_raw, index, play_order, loop=loop,
                               enqueued=enqueued, current_token=current_token)
        return _make_handler_input(user_attr)

    def _patch_stream(self, audio_url='http://stream'):
        return mock.patch.object(
            player.Api, 'get_stream',
            return_value=(_node(audio_url=audio_url), None))

    def _enqueued(self, hi):
        directives = hi.response_builder.directives
        if not directives:
            return None
        return directives[0].audio_item.stream

    def test_already_enqueued_returns_false(self):
        hi = self._hi([_raw('A'), _raw('B')], index=0, play_order=[0, 1],
                      enqueued=True)
        with mock.patch.object(player.Api, 'next_track') as next_mock:
            self.assertFalse(player.Controller.enqueue_next_stream(hi))
            next_mock.assert_not_called()
        self.assertEqual(hi.response_builder.directives, [])

    def test_no_playlist_returns_false(self):
        hi = self._hi([], index=0, play_order=[])
        self.assertFalse(player.Controller.enqueue_next_stream(hi))

    def test_no_current_metadata_returns_false(self):
        hi = self._hi([_raw('A'), _raw('B')], index=5, play_order=[0, 1])
        self.assertFalse(player.Controller.enqueue_next_stream(hi))

    def test_authoritative_next_wins_over_stale_window(self):
        # The server says the reordered C is next, but the skill's stale window
        # would have picked B. C must be enqueued (the regression this fixes).
        hi = self._hi([_raw('A'), _raw('B'), _raw('C')], index=0,
                      play_order=[0, 1, 2], current_token='0|A')
        with mock.patch.object(player.Api, 'next_track',
                               return_value=(_meta('C'), None)), \
             self._patch_stream():
            self.assertTrue(player.Controller.enqueue_next_stream(hi))
        stream = self._enqueued(hi)
        self.assertEqual(stream.token, '1|C')
        self.assertEqual(stream.url, 'http://stream')
        self.assertEqual(stream.expected_previous_token, '0|A')
        attrs = hi.attributes_manager.persistent_attributes['test-user']
        self.assertEqual(attrs['playback_info']['play_order'], [0, 2, 1])
        self.assertEqual(attrs['playback_info']['next_stream_enqueued'], True)

    def test_falls_back_to_window_when_server_has_no_next(self):
        hi = self._hi([_raw('A'), _raw('B'), _raw('C')], index=0,
                      play_order=[0, 1, 2], current_token='0|A')
        with mock.patch.object(player.Api, 'next_track',
                               return_value=(None, None)), \
             self._patch_stream():
            self.assertTrue(player.Controller.enqueue_next_stream(hi))
        self.assertEqual(self._enqueued(hi).token, '1|B')

    def test_loop_wraps_at_window_end(self):
        hi = self._hi([_raw('A'), _raw('B')], index=1, play_order=[0, 1],
                      loop=True, current_token='1|B')
        with mock.patch.object(player.Api, 'next_track',
                               return_value=(None, None)), \
             self._patch_stream():
            self.assertTrue(player.Controller.enqueue_next_stream(hi))
        self.assertEqual(self._enqueued(hi).token, '0|A')

    def test_extends_window_at_end_then_enqueues(self):
        hi = self._hi([_raw('A'), _raw('B')], index=1, play_order=[0, 1],
                      current_token='1|B')

        def _extend(handler_input):
            user_attr = handler_input.attributes_manager.persistent_attributes['test-user']
            user_attr['playlist'].append(_raw('C'))
            user_attr['playback_info']['play_order'] = [0, 1, 2]
            return True

        with mock.patch.object(player.Api, 'next_track',
                               return_value=(None, None)), \
             mock.patch.object(player.Controller, 'extend_queue',
                               side_effect=_extend), \
             self._patch_stream():
            self.assertTrue(player.Controller.enqueue_next_stream(hi))
        self.assertEqual(self._enqueued(hi).token, '2|C')

    def test_returns_false_when_extend_fails_at_end(self):
        hi = self._hi([_raw('A'), _raw('B')], index=1, play_order=[0, 1],
                      current_token='1|B')
        with mock.patch.object(player.Api, 'next_track',
                               return_value=(None, None)), \
             mock.patch.object(player.Controller, 'extend_queue',
                               return_value=False):
            self.assertFalse(player.Controller.enqueue_next_stream(hi))
        self.assertEqual(hi.response_builder.directives, [])

    def test_returns_false_and_resets_flag_on_stream_error(self):
        hi = self._hi([_raw('A'), _raw('B')], index=0, play_order=[0, 1],
                      current_token='0|A')
        with mock.patch.object(player.Api, 'next_track',
                               return_value=(None, None)), \
             mock.patch.object(player.Api, 'get_stream',
                               return_value=(None, Exception('no stream'))):
            self.assertFalse(player.Controller.enqueue_next_stream(hi))
        attrs = hi.attributes_manager.persistent_attributes['test-user']
        self.assertEqual(attrs['playback_info']['next_stream_enqueued'], False)

    def test_adjusts_index_when_pulling_already_played_track(self):
        # B playing (index 1); server says A plays next (re-queued).
        hi = self._hi([_raw('A'), _raw('B'), _raw('C')], index=1,
                      play_order=[0, 1, 2], current_token='1|B')
        with mock.patch.object(player.Api, 'next_track',
                               return_value=(_meta('A'), None)), \
             self._patch_stream():
            self.assertTrue(player.Controller.enqueue_next_stream(hi))
        self.assertEqual(self._enqueued(hi).token, '1|A')
        attrs = hi.attributes_manager.persistent_attributes['test-user']
        self.assertEqual(attrs['playback_info']['index'], 0)
        self.assertEqual(attrs['playback_info']['play_order'], [1, 0, 2])

    def test_new_track_queued_by_user_is_appended_and_enqueued(self):
        # Server resolves a brand-new Z (added via the web remote) as next.
        hi = self._hi([_raw('A'), _raw('B')], index=0, play_order=[0, 1],
                      current_token='0|A')
        with mock.patch.object(player.Api, 'next_track',
                               return_value=(_meta('Z'), None)), \
             self._patch_stream():
            self.assertTrue(player.Controller.enqueue_next_stream(hi))
        self.assertEqual(self._enqueued(hi).token, '1|Z')
        attrs = hi.attributes_manager.persistent_attributes['test-user']
        self.assertEqual([m['video_id'] for m in attrs['playlist']],
                         ['A', 'B', 'Z'])


if __name__ == '__main__':
    unittest.main()
