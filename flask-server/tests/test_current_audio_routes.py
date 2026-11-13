"""Accepted playback transitions, not speculative HTTP fetches, own retained audio."""
import unittest
from unittest import mock
from test_audio_endpoint import server
from test_youtube_stream import Upstream
from current_audio import CurrentAudio
from youtube_stream import StreamError

A='aaaaaaaaaaa'
B='bbbbbbbbbbb'


class CurrentAudioRoutes(unittest.TestCase):
    def setUp(self):
        self.current=CurrentAudio();self.addCleanup(self.current.close)
        self.current.activate(A)
        self.current.write(self.current.ticket(A),'"old"',4,0,b'old!')
        self.old=self.current.file
        state=dict(server._now_playing,video_id=A,playing=True,playback_confirmed=True,
                   playback_processing=False,duration_ms=180000,queue=[])
        patches=[mock.patch.object(server._youtube_stream_for(),'current',self.current),
                 mock.patch.object(server,'_now_playing',state),
                 mock.patch.object(server,'_schedule_alexa_queue_sync'),
                 mock.patch.object(server._playback_persistence,'submit'),
                 mock.patch.object(server,'_refresh_radio_queue'),
                 mock.patch.object(server,'_lookup_and_update_np'),
                 mock.patch.object(server._youtube_stream_for(),'resolve',return_value={
                     'url':'https://rr1.googlevideo.com/audio','itag':140,'length':12,'modified':'1'}),
                 mock.patch.object(server._youtube_stream_for().opener,'open',side_effect=lambda *a,**kw:Upstream())]
        for patch in patches:patch.start();self.addCleanup(patch.stop)
        self.client=server.app.test_client();self.headers={'X-Api-Key':server.API_KEY}

    def test_selection_and_url_prefetch_keep_previous_audio_until_start(self):
        server._update_now_playing(video_id=B,playing=False,playback_confirmed=False)
        with mock.patch.object(server,'_ensure_audio_ready_for_play'):
            r=self.client.get('/audio/?video_id='+B+'&prefetch=1',headers=self.headers)
        self.assertEqual(r.status_code,202);self.assertIs(self.current.file,self.old)
        self.assertFalse(self.old.closed);self.assertEqual(self.current.video,A)

    def test_first_current_audio_replaces_old_and_replay_uses_retained_bytes(self):
        server._update_now_playing(video_id=B,playing=False,playback_confirmed=False)
        r=self.client.get('/audio/?video_id='+B,headers=self.headers)
        self.assertEqual(r.data,b'123456789012');r.close()
        self.assertTrue(self.old.closed);self.assertEqual(self.current.video,B)
        with mock.patch.object(server._youtube_stream_for(),'resolve',side_effect=AssertionError('network on replay')):
            replay=self.client.get('/audio/?video_id='+B,headers=self.headers)
            self.assertEqual(replay.headers['X-Audio-Source'],'current-track')
            self.assertEqual(replay.data,b'123456789012');replay.close()

    def test_echo_next_track_prebuffer_does_not_evict_current(self):
        r=self.client.get('/proxy/?video_id='+B,headers=self.headers)
        self.assertEqual(r.status_code,200);self.assertEqual(r.data,b'123456789012');r.close()
        self.assertIs(self.current.file,self.old);self.assertFalse(self.old.closed)
        self.assertEqual(self.current.video,A)

    def test_real_started_event_releases_old_bytes(self):
        server._update_now_playing(video_id=B,playing=True,playback_confirmed=True)
        self.assertTrue(self.old.closed);self.assertEqual(self.current.video,B)

    def test_pause_resume_and_same_track_handoff_keep_bytes(self):
        server._update_now_playing(playing=False,playback_confirmed=True)
        self.assertIs(self.current.file,self.old)
        server._update_now_playing(playing=True,playback_confirmed=True)
        self.assertIs(self.current.file,self.old);self.assertFalse(self.old.closed)

    def test_failed_next_track_keeps_previous_bytes(self):
        server._update_now_playing(video_id=B,playing=False,playback_confirmed=False)
        with mock.patch.object(server._youtube_stream_for(),'open',side_effect=StreamError('track_unavailable',404)):
            r=self.client.get('/audio/?video_id='+B,headers=self.headers)
        self.assertEqual(r.status_code,404);self.assertIs(self.current.file,self.old)

    def test_head_and_metadata_do_not_start_selected_song(self):
        server._update_now_playing(video_id=B,playing=False,playback_confirmed=False)
        head=self.client.head('/audio/?video_id='+B,headers=self.headers);head.close()
        info=self.client.get('/audio/?video_id='+B+'&info=1',headers=self.headers)
        self.assertEqual(head.status_code,200);self.assertEqual(info.status_code,200)
        self.assertIs(self.current.file,self.old)

    def test_download_of_another_song_does_not_replace_current(self):
        r=self.client.get('/audio/?video_id='+B,headers={**self.headers,'X-MusicBox-Download':'1'})
        self.assertEqual(r.data,b'123456789012');r.close()
        self.assertIn('attachment',r.headers['Content-Disposition']);self.assertIs(self.current.file,self.old)

    def test_stale_audio_request_cannot_restore_previous_track(self):
        server._update_now_playing(video_id=B,playing=True,playback_confirmed=True)
        r=self.client.get('/audio/?video_id='+A,headers=self.headers)
        self.assertEqual(r.data,b'123456789012');r.close()
        self.assertEqual(self.current.video,B);self.assertIsNone(self.current.file)

    def test_clearing_playback_releases_last_audio(self):
        server._update_now_playing(video_id='',playing=False)
        self.assertTrue(self.old.closed);self.assertEqual(self.current.video,'')


if __name__=='__main__':unittest.main()
