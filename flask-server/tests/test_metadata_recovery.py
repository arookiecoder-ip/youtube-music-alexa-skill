"""Exercise real metadata/enrichment functions without contacting YouTube."""
import ast
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

SOURCE = Path(__file__).resolve().parents[1] / 'server.py'
VIDEO = 'AAAAAAAAAAA'

def namespace(queued=None, streaming=None):
    relay = SimpleNamespace(enabled=True, metadata=Mock(return_value=streaming))
    yt = SimpleNamespace(get_song=Mock(return_value={}), get_watch_playlist=Mock(return_value={}))
    ns = dict(_valid_video_id=lambda v: v == VIDEO, _metadata_from_queue=lambda v: queued,
        _youtube_stream=relay, _get_ytmusic=lambda: yt, _clean_artist_credit=lambda v: v or '',
        Supporting=SimpleNamespace(probe_metadata=Mock(return_value=None)), logger=Mock())
    nodes = [n for n in ast.parse(SOURCE.read_text()).body if isinstance(n, ast.FunctionDef)
        and n.name in ('_lookup_video_metadata', '_lookup_and_update_np')]
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), 'exec'), ns)
    return ns, yt, relay

def test_placeholder_queue_title_and_duration_use_streaming_metadata_without_subprocess():
    ns, yt, relay = namespace({'title': 'YouTube track', 'duration_ms': 0},
        {'title':'Actual title', 'artist':'Artist', 'duration_ms':185000})
    metadata = ns['_lookup_video_metadata'](VIDEO)
    assert metadata['title'] == 'Actual title' and metadata['duration_ms'] == 185000
    yt.get_song.assert_not_called()
    ns['Supporting'].probe_metadata.assert_not_called()

def test_authoritative_queue_title_is_kept_while_missing_duration_is_repaired():
    ns, _, _ = namespace({'title':'Album title', 'artist':'Album artist', 'duration_ms':0},
                         {'title':'Uploader title', 'artist':'Uploader', 'duration_ms':123000})
    result=ns['_lookup_video_metadata'](VIDEO)
    assert result['title']=='Album title' and result['artist']=='Album artist' and result['duration_ms']==123000

def test_catalog_exception_still_reaches_metadata_fallback():
    ns, yt, relay = namespace()
    relay.metadata.side_effect=RuntimeError('stream metadata unavailable')
    yt.get_song.side_effect=RuntimeError('catalog unavailable')
    ns['Supporting'].probe_metadata.return_value={'title':'Fallback video','duration_ms':12000}
    assert ns['_lookup_video_metadata'](VIDEO)['title']=='Fallback video'

def test_live_duration_is_not_invented_or_repeatedly_probed():
    ns, yt, _=namespace(streaming={'title':'Live broadcast','artist':'Host','duration_ms':0,'is_live':True})
    assert ns['_lookup_video_metadata'](VIDEO)['duration_ms']==0
    yt.get_song.assert_not_called(); ns['Supporting'].probe_metadata.assert_not_called()

def test_late_metadata_cannot_overwrite_new_song_and_queue_identity_is_preserved():
    ns, _, _=namespace()
    state={'video_id':VIDEO,'title':'YouTube track','position_ms':4000,'duration_ms':0,
           'queue':[{'video_id':VIDEO,'title':'YouTube track','entry_id':'entry','duration_ms':0}]}
    ns.update(_np_lookup_lock=threading.Lock(),_np_lookup_inflight=set(),_np_lock=threading.Lock(),
        _now_playing=state,time=time,_notify_sse=Mock(),_thumbnail_url=lambda x:x,_update_now_playing=lambda **kw:state.update(kw))
    ns['_lookup_video_metadata']=lambda v:{'title':'Resolved','artist':'Artist','duration_ms':10000}
    ns['_lookup_and_update_np'](VIDEO)
    assert state['title']=='Resolved' and state['queue'][0]['duration_ms']==10000
    assert state['queue'][0]['entry_id']=='entry' and state['position_ms']==4000
    def delayed(v):
        state.update(video_id='BBBBBBBBBBB',title='New song')
        return {'title':'Old result','duration_ms':9999}
    ns['_lookup_video_metadata']=delayed
    ns['_lookup_and_update_np'](VIDEO)
    assert state['title']=='New song'

def test_complete_queue_metadata_does_not_make_network_requests():
    ns, yt, relay=namespace({'title':'Known', 'artist':'Artist','duration_ms':15000})
    assert ns['_lookup_video_metadata'](VIDEO)['duration_ms']==15000
    relay.metadata.assert_not_called();yt.get_song.assert_not_called()

def test_malformed_durations_cannot_prevent_a_valid_fallback():
    for value in [None, True, -1, 'nan', float('inf')]:
        ns, yt, relay=namespace(streaming={'title':'Resolved','duration_ms':value})
        yt.get_song.return_value={'videoDetails':{'title':'Catalog title','lengthSeconds':'42'}}
        result=ns['_lookup_video_metadata'](VIDEO)
        assert result['duration_ms']==42000 and result['title']=='Resolved'

def test_artist_enrichment_cannot_deadlock_or_delay_title_publication():
    ns, yt, _=namespace()
    state={'video_id':VIDEO,'title':'','queue':[]}
    ns.update(_np_lookup_lock=threading.Lock(),_np_lookup_inflight=set(),_np_lock=threading.Lock(),
        _now_playing=state,time=time,_notify_sse=Mock(),_thumbnail_url=lambda x:x,
        _artist_entries_from_item=lambda row:row['artists'],_artist_credit_from_list=lambda rows:'Artist')
    ns['_lookup_video_metadata']=lambda v:{'title':'Resolved','duration_ms':10000}
    def watch(**kwargs):
        assert state['title']=='Resolved'
        assert ns['_notify_sse'].call_count==1
        return {'tracks':[{'videoId':VIDEO,'artists':[{'name':'Artist','id':'UCartist'}]}]}
    yt.get_watch_playlist.side_effect=watch
    worker=threading.Thread(target=ns['_lookup_and_update_np'],args=(VIDEO,),daemon=True)
    worker.start();worker.join(1)
    assert not worker.is_alive(), 'Metadata enrichment re-entered the non-reentrant state lock'
    assert state['artists']==[{'name':'Artist','id':'UCartist'}]
