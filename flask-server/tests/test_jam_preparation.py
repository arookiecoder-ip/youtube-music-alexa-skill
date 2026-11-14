import unittest
from unittest.mock import Mock
from jam_preparation import JamPreparation
from test_jam_rooms import JamRoomsTests


class JamPreparationTests(JamRoomsTests):
    def test_radio_extends_shared_queue_without_changing_current_entry_or_position(self):
        state=self.command('queue',tracks=self.songs()[:1])
        state=self.command('seek',position_ms=1234)
        self.assertTrue(self.rooms.extend_radio('room',state['queue_version'],state['video_id'],self.songs()))
        updated=self.state()
        self.assertEqual(len(updated['queue']),3)
        self.assertEqual(updated['entry_id'],state['entry_id'])
        self.assertEqual(updated['position_ms'],state['position_ms'])
        self.assertEqual(updated['queue_version'],state['queue_version']+1)

    def test_late_radio_never_overwrites_new_queue_or_explicit_queue_edits(self):
        state=self.state()
        self.command('queue',tracks=self.songs()[1:])
        self.assertFalse(self.rooms.extend_radio('room',state['queue_version'],state['video_id'],self.songs()))
        self.assertEqual(len(self.state()['queue']),2)

    def test_scheduling_does_not_wait_for_provider_and_uses_host_identity(self):
        jobs=[];radio=Mock(return_value=self.songs());warm=Mock()
        worker=JamPreparation(self.rooms,lambda _:True,radio,warm,lambda identity,job:jobs.append((identity,job)))
        state=self.command('queue',tracks=self.songs()[:1])
        worker.schedule('room',state,'user:host',True)
        worker.schedule('room',state,'user:host',True)
        self.assertEqual(len(jobs),1);radio.assert_not_called();warm.assert_not_called()
        self.assertEqual(jobs[0][0],'user:host')
        jobs[0][1]()
        self.assertEqual(len(self.state()['queue']),3)
        self.assertEqual(warm.call_args_list[0].args,(state['video_id'],))
        self.assertEqual(warm.call_args_list[-1].args,(self.songs()[1]['video_id'],))

    def test_ended_room_does_not_start_provider_work(self):
        radio=Mock();warm=Mock()
        worker=JamPreparation(self.rooms,lambda _:False,radio,warm,lambda identity,job:job())
        worker.schedule('room',self.state(),'user:host',True)
        radio.assert_not_called();warm.assert_not_called()
