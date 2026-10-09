"""Validate production directives with the actual ASK SDK in an isolated process."""
import os
import subprocess
import sys
import unittest


class RealSdkQueueTests(unittest.TestCase):
    def test_replace_and_clear_directives_serialize_with_supported_fields(self):
        code = r'''
import sys
from types import SimpleNamespace as Node
from unittest.mock import patch
sys.path.insert(0, 'lambda')
from mediaUtils import player
from ask_sdk_core.response_helper import ResponseFactory
from ask_sdk_core.serialize import DefaultSerializer
from models.player_models import Metadata
info = dict(index=0, play_order=[0,1], current_token='0|A|attempt', offset_in_ms=9000,
    stream_url='https://cached/A', stream_url_video_id='A')
attrs = dict(playback_info=info, playback_setting=dict(loop=False,shuffle=False), playlist=[
    dict(video_id=v,title=v,artist='artist',thumbnail=None,duration_ms=180000) for v in ['A','B']])
hi = Node(response_builder=ResponseFactory(), attributes_manager=Node(persistent_attributes={'user':attrs}),
    request_envelope=Node(context=Node(system=Node(user=Node(user_id='user')),audio_player=Node(token='0|A|attempt'))))
def next_track(*args):
    info['next_decision']=dict(authoritative=True,permitted=True,decision_id='rev')
    return Metadata(title='C',artist='artist',video_id='C',thumbnail=None,duration_ms=180000),None
with patch.object(player.Api,'next_track',side_effect=next_track), patch.object(player.Api,'get_stream',return_value=(Node(audio_url='https://cached/C'),None)), patch.object(player.Api,'validate_next',return_value=True):
    response=player.Controller.replace_pending(hi,'0|A|attempt')
data=DefaultSerializer().serialize(response)
directive=data['directives'][0]
assert directive['playBehavior']=='REPLACE_ENQUEUED',data
assert 'expectedPreviousToken' not in directive['audioItem']['stream'],data
assert directive['audioItem']['stream']['token']=='1|C|qrev',data
assert info['current_token']=='0|A|attempt' and info['offset_in_ms']==9000,info
hi.response_builder=ResponseFactory()
with patch.object(player.Api,'next_track',return_value=(None,None)), patch.object(player.Api,'validate_next',return_value=True):
    response=player.Controller.replace_pending(hi,'0|A|attempt')
data=DefaultSerializer().serialize(response)
assert data['directives'][0]==dict(type='AudioPlayer.ClearQueue',clearBehavior='CLEAR_ENQUEUED'),data

# Run the actual started-handler body without constructing its AWS SkillBuilder.
import ast, logging
from pathlib import Path
source=Path('lambda/lambda_function.py').read_text()
cls=next(n for n in ast.parse(source).body if isinstance(n,ast.ClassDef) and n.name=='PlaybackStartedEventHandler')
fn=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='handle')
namespace=dict(player=player,logger=logging.getLogger('test'))
exec(compile(ast.Module(body=[fn],type_ignores=[]),'lambda_function.py','exec'),namespace)
hi.response_builder=ResponseFactory()
hi.request_envelope.request=Node(token='1|B|qold',offset_in_milliseconds=0)
hi.request_envelope.context.audio_player.token='1|B|qold'
before=dict(info)
with patch.object(player,'_notify_server',return_value=dict(ignored='superseded queued start',stop=False)), patch.object(player.Controller,'expand_radio_queue') as expand:
    response=namespace['handle'](None,hi)
assert info==before,info
expand.assert_not_called()
assert not response.directives,response
'''
        root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
        result = subprocess.run([sys.executable, '-c', code], cwd=root, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
