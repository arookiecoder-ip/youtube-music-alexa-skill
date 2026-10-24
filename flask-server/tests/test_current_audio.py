"""The only retained audio belongs to the current playback generation."""
import os
import unittest
from unittest.mock import Mock, patch
from current_audio import CurrentAudio, byte_bounds
from youtube_stream import YouTubeStream, StreamError
from test_youtube_stream import Upstream, MEDIA, VID


class CurrentTrack(unittest.TestCase):
    def setUp(self):
        self.current=CurrentAudio();self.addCleanup(self.current.close)

    def fill(self, video='aaaaaaaaaaa', data=b'abcdefghijkl'):
        self.current.activate(video)
        ticket=self.current.ticket(video)
        self.current.write(ticket,'"same"',len(data),0,data)
        return ticket

    def test_current_track_replays_complete_bytes_and_seeks(self):
        self.fill()
        for value,expected in [(None,b'abcdefghijkl'),('bytes=3-5',b'def'),('bytes=-4',b'ijkl'),('bytes=10-',b'kl')]:
            r=self.current.open('aaaaaaaaaaa',value)
            self.assertEqual(r.read1(100),expected);self.assertEqual(r.read1(100),b'');r.close()

    def test_pause_and_same_track_handoff_preserve_file_and_bytes(self):
        self.fill();file=self.current.file;epoch=self.current.epoch
        self.current.activate('aaaaaaaaaaa')
        self.assertIs(self.current.file,file);self.assertEqual(self.current.epoch,epoch)
        self.assertTrue(self.current.has('aaaaaaaaaaa'))

    def test_next_track_closes_previous_cache(self):
        self.fill();old=self.current.file
        self.current.activate('bbbbbbbbbbb')
        self.assertTrue(old.closed);self.assertFalse(self.current.has('aaaaaaaaaaa'))
        self.assertIsNone(self.current.open('aaaaaaaaaaa'))

    def test_late_old_writer_cannot_resurrect_after_a_b_a_cycle(self):
        ticket=self.fill();self.current.activate('bbbbbbbbbbb');self.current.activate('aaaaaaaaaaa')
        self.current.write(ticket,'"same"',12,0,b'abcdefghijkl')
        self.assertIsNone(self.current.file)

    def test_next_track_prefetch_never_displaces_current(self):
        self.fill();ticket=self.current.ticket('bbbbbbbbbbb')
        self.current.write(ticket,'"next"',4,0,b'next')
        self.assertEqual(self.current.video,'aaaaaaaaaaa');self.assertTrue(self.current.has('aaaaaaaaaaa'))

    def test_first_bytes_can_bind_when_request_becomes_current(self):
        self.fill();ticket=self.current.ticket('bbbbbbbbbbb')
        self.current.activate('bbbbbbbbbbb');self.current.write(ticket,'"next"',4,0,b'next')
        r=self.current.open('bbbbbbbbbbb');self.assertEqual(r.read1(10),b'next');r.close()

    def test_sparse_holes_never_appear_as_zero_filled_valid_audio(self):
        self.current.activate('aaaaaaaaaaa');ticket=self.current.ticket('aaaaaaaaaaa')
        self.current.write(ticket,'"same"',12,0,b'abcd')
        self.current.write(ticket,'"same"',12,8,b'ijkl')
        self.assertIsNone(self.current.open('aaaaaaaaaaa'))
        self.assertIsNone(self.current.open('aaaaaaaaaaa','bytes=2-9'))
        self.current.write(ticket,'"same"',12,4,b'efgh')
        r=self.current.open('aaaaaaaaaaa');self.assertEqual(r.read1(100),b'abcdefghijkl');r.close()

    def test_existing_reader_safely_finishes_after_track_is_removed(self):
        self.fill();r=self.current.open('aaaaaaaaaaa')
        self.current.activate('bbbbbbbbbbb')
        self.assertEqual(r.read1(100),b'abcdefghijkl');r.close()
        self.assertIsNone(self.current.open('aaaaaaaaaaa'))

    def test_representation_change_does_not_splice_encodings(self):
        self.fill();ticket=self.current.ticket('aaaaaaaaaaa')
        self.current.write(ticket,'"changed"',12,8,b'WXYZ')
        self.assertIsNone(self.current.open('aaaaaaaaaaa'))
        r=self.current.open('aaaaaaaaaaa','bytes=8-11');self.assertEqual(r.read1(4),b'WXYZ');r.close()

    def test_head_and_if_range_follow_same_representation(self):
        self.fill()
        r=self.current.open('aaaaaaaaaaa','bytes=2-3','"wrong"',head=True)
        self.assertEqual(r.status,200);self.assertEqual(r.headers['Content-Length'],'12');self.assertEqual(r.read1(1),b'');r.close()

    def test_disk_full_failure_drops_temporary_data_without_raising(self):
        self.current.factory=Mock(side_effect=OSError('disk full'))
        self.fill();self.assertIsNone(self.current.file)

    def test_one_track_safety_ceiling_never_blocks_streaming(self):
        self.current.MAX_BYTES=4;self.fill(data=b'abcdefgh')
        self.assertIsNone(self.current.file)

    def test_clear_queue_deletes_the_last_track(self):
        self.fill();file=self.current.file;self.current.activate('')
        self.assertTrue(file.closed);self.assertEqual(self.current.video,'')

    def test_file_has_no_persistent_name_and_is_unlinked(self):
        self.fill()
        self.assertIsInstance(self.current.file.name,int)
        self.assertEqual(os.fstat(self.current.file.fileno()).st_nlink,0)

    def test_invalid_ranges_do_not_read(self):
        self.fill()
        for value in ['bytes=20-','bytes=-0','bytes=3-1','bytes=0-1,3-4']:
            with self.assertRaises(ValueError):self.current.open('aaaaaaaaaaa',value)


class RelayReuse(unittest.TestCase):
    def test_cached_unsatisfiable_range_reports_the_complete_length(self):
        current=CurrentAudio();self.addCleanup(current.close);current.activate(VID)
        current.write(current.ticket(VID),'"current"',12,0,b'123456789012')
        stream=YouTubeStream(lambda:'',current=current);self.addCleanup(stream.close)
        with self.assertRaises(StreamError) as error:stream.open(VID,byte_range='bytes=12-')
        self.assertEqual(error.exception.status,416)
        self.assertEqual(error.exception.headers['Content-Range'],'bytes */12')

    def test_unreadable_current_audio_falls_back_to_youtube(self):
        current=CurrentAudio();self.addCleanup(current.close);current.activate(VID)
        current.write(current.ticket(VID),'"current"',12,0,b'123456789012')
        opener=Mock();opener.open.return_value=Upstream()
        stream=YouTubeStream(lambda:'',current=current,opener=opener)
        self.addCleanup(stream.close);stream.resolve=Mock(return_value=MEDIA)
        with patch('current_audio.os.pread',side_effect=OSError('unreadable')):
            relay=stream.open(VID)
        self.assertEqual(relay.headers['X-Audio-Source'],'youtube')
        self.assertEqual(b''.join(relay.chunks()),b'123456789012')
        self.assertIsNone(current.file)

    def test_current_replay_bypasses_youtube_but_other_songs_do_not_replace_it(self):
        current=CurrentAudio();self.addCleanup(current.close);current.activate(VID)
        opener=Mock();opener.open.return_value=Upstream()
        stream=YouTubeStream(lambda:'',current=current,opener=opener)
        self.addCleanup(stream.close);stream.resolve=Mock(return_value=MEDIA)
        r=stream.open(VID)
        for block in r.chunks():r.remember(block)
        replay=stream.open(VID);self.assertEqual(b''.join(replay.chunks()),b'123456789012')
        self.assertEqual(replay.headers['X-Audio-Source'],'current-track')
        self.assertEqual(opener.open.call_count,1)


if __name__=='__main__':unittest.main()
