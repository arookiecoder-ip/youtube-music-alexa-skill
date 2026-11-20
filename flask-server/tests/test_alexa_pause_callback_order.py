"""A newly resumed stream can stop before its Started callback is delivered."""
from datetime import datetime, timezone
from unittest import mock
from test_next_track import NextTrackBase, server, _meta

VIDEO='AAAAAAAAAAA'
def token(nonce):
    return '0|'+VIDEO+'|'+nonce*24+'|b'+'d'*64

class PauseCallbackOrderTests(NextTrackBase):
    def setUp(self):
        super().setUp()
        self.output={'playback_output':'alexa','output_token':'lease','output_serial':self.SERIAL}
        mock.patch.object(server._playback_output,'snapshot',return_value=self.output).start()
        mock.patch.object(server,'_notify_sse').start()
        mock.patch.object(server,'_bg_thread').start()
        mock.patch.object(server,'_claim_alexa_output').start()
        server._now_playing.update(video_id=VIDEO,queue=[_meta(VIDEO)],queue_index=0,
            playing=False,playback_confirmed=True,playback_processing=True,
            playback_revision=5,position_ms=12000,started_at=100,alexa_intent_at=100.25,
            alexa_token=token('a'),_alexa_pause_pending={
                'video_id':VIDEO,'intent_at':100.25,'output_token':'lease'})

    def event(self,event,nonce='b',at=100,video=VIDEO):
        stamp=datetime.fromtimestamp(at,timezone.utc).isoformat(timespec='seconds')
        return self.client.post('/alexa/state_event/?key='+server.API_KEY,json={
            'event':event,'video_id':video,'token':token(nonce),
            'offset_in_ms':12000,'event_timestamp':stamp})

    def test_stop_before_start_settles_pause_for_the_new_resume_token(self):
        result=self.event('stopped')
        self.assertNotIn('ignored',result.json)
        self.assertFalse(server._now_playing['playback_processing'])
        self.assertFalse(server._now_playing['playing'])
        self.assertEqual(server._now_playing['_alexa_stop_ack']['token'],token('b'))

    def test_same_second_late_started_cannot_reanimate_stopped_token(self):
        self.event('stopped')
        result=self.event('started')
        self.assertIn('ignored',result.json)
        self.assertFalse(server._now_playing['playing'])
        self.assertFalse(server._now_playing['playback_processing'])
        self.assertEqual(server._now_playing['position_ms'],12000)

    def test_old_stop_stays_ignored(self):
        result=self.event('stopped',at=99)
        self.assertIn('ignored',result.json)
        self.assertTrue(server._now_playing['playback_processing'])

    def test_output_handoff_stays_fenced(self):
        self.output.update(playback_output='phone',output_token='phone-lease')
        result=self.event('stopped')
        self.assertIn('ignored',result.json)
        self.assertNotIn('_alexa_stop_ack',server._now_playing)

    def test_newer_resume_cannot_be_stopped_by_the_old_pause(self):
        server._now_playing.update(alexa_intent_at=101,playing=True)
        result=self.event('stopped',at=102)
        self.assertIn('ignored',result.json)
        self.assertTrue(server._now_playing['playing'])
        self.assertNotIn('_alexa_stop_ack',server._now_playing)

    def test_other_track_stop_stays_ignored(self):
        result=self.event('stopped',video='BBBBBBBBBBB')
        self.assertIn('ignored',result.json)
        self.assertTrue(server._now_playing['playback_processing'])

    def test_new_unique_resume_can_start_after_the_acknowledged_stop(self):
        self.event('stopped')
        server._now_playing.update(alexa_intent_at=101,playing=True,playback_confirmed=False,
                                  playback_processing=True)
        result=self.event('started',nonce='c',at=102)
        self.assertNotIn('ignored',result.json)
        self.assertTrue(server._now_playing['playing'])
        self.assertEqual(server._now_playing['alexa_token'],token('c'))

    def test_newer_intent_during_callback_handling_cannot_be_overwritten(self):
        def resume(*args,**kwargs):
            server._now_playing.update(alexa_intent_at=101,playing=True)
        with mock.patch.object(server,'_record_volume_state',side_effect=resume):
            result=self.client.post('/alexa/state_event/?key='+server.API_KEY,json={
                'event':'stopped','video_id':VIDEO,'token':token('b'),'volume':30,
                'event_timestamp':datetime.fromtimestamp(100,timezone.utc).isoformat(timespec='seconds')})
        self.assertEqual(result.json.get('ignored'),'superseded pause')
        self.assertTrue(server._now_playing['playing'])
        self.assertNotIn('_alexa_stop_ack',server._now_playing)

    def test_pause_dispatch_installs_fence_before_synchronous_new_token_stop(self):
        import time
        server._now_playing.update(playing=True,playback_processing=False,started_at=time.time())
        server._now_playing.pop('_alexa_pause_pending')
        def send(*args):
            self.assertEqual(server._now_playing['_alexa_pause_pending']['intent_at'],
                             server._now_playing['alexa_intent_at'])
            self.event('stopped',at=time.time())
            return None
        with mock.patch.object(server.alexa_remote.remote,'command',side_effect=send,create=True):
            result=self.client.post('/alexa/command/?key='+server.API_KEY,json={
                'serial':self.SERIAL,'action':'pause'})
        self.assertEqual(result.status_code,200)
        self.assertFalse(server._now_playing['playback_processing'])
        self.assertEqual(server._now_playing['_alexa_stop_ack']['token'],token('b'))

    def test_legacy_reusable_tokens_do_not_permanently_block_resume(self):
        legacy='0|'+VIDEO
        server._now_playing.update(alexa_intent_at=101,playing=True,alexa_token=legacy,
                                  _alexa_stop_ack={'token':legacy})
        result=self.client.post('/alexa/state_event/?key='+server.API_KEY,json={
            'event':'started','video_id':VIDEO,'token':legacy,
            'event_timestamp':datetime.fromtimestamp(102,timezone.utc).isoformat(timespec='seconds')})
        self.assertNotIn('ignored',result.json)
        self.assertTrue(server._now_playing['playing'])
