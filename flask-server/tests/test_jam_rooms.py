"""Real SQLite tests for independent Jam playback and persistent membership."""
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
import tempfile
import unittest

from jam_members import JamMembers
from jam_rooms import JamRooms, JamConflict


class JamRoomsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / 'jam.db')
        self.now = 1000.0
        @contextmanager
        def database():
            db = sqlite3.connect(self.path, timeout=10)
            db.row_factory = sqlite3.Row
            try:
                yield db
            finally:
                db.close()
        self.database = database
        with database() as db:
            db.execute('CREATE TABLE jam_tokens(token TEXT PRIMARY KEY, created_at REAL, user_id TEXT)')
            db.execute("INSERT INTO jam_tokens VALUES('room',1000,'host')")
            db.commit()
        self.members = JamMembers(database)
        self.members.ensure()
        self.rooms = JamRooms(database, lambda: self.now)
        self.rooms.start('room', {'queue': self.songs(), 'queue_index':0, 'playing':True}, 'host-phone')
        self.count = 0

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def songs():
        return [{'video_id': 'aaaaaaaaaaa', 'title':'First', 'duration_ms':10000},
                {'video_id': 'bbbbbbbbbbb', 'title':'Second', 'duration_ms':20000},
                {'video_id': 'ccccccccccc', 'title':'Third', 'duration_ms':30000}]

    def state(self):
        return self.rooms.transact('room')

    def command(self, action, **fields):
        self.count += 1
        return self.rooms.transact('room', action, dict(command_id=f'command-{self.count}', revision=self.state()['revision'], **fields))

    def test_one_clock_advances_once_despite_many_devices_polling(self):
        self.now += 12
        for _ in range(10):
            state=self.state()
            self.assertEqual((state['queue_index'], state['position_ms']), (1,2000))

    def test_disconnected_devices_catch_up_without_replaying_finished_tracks(self):
        self.now += 35
        self.assertEqual((self.state()['queue_index'], self.state()['position_ms']), (2,5000))

    def test_end_of_queue_stops_at_end_and_does_not_restart(self):
        self.now += 100
        state=self.state()
        self.assertFalse(state['playing'])
        self.assertEqual((state['queue_index'],state['position_ms']), (2,30000))

    def test_pause_freezes_clock_and_seek_is_clamped(self):
        self.now += 2
        self.command('pause')
        self.now += 40
        self.assertEqual(self.state()['position_ms'],2000)
        self.command('seek',position_ms=999999)
        self.assertEqual(self.state()['position_ms'],10000)

    def test_duplicate_retry_does_not_skip_twice(self):
        body=dict(command_id='retry',revision=self.state()['revision'])
        self.rooms.transact('room','next',body)
        self.rooms.transact('room','next',body)
        self.assertEqual(self.state()['queue_index'],1)

    def test_stale_queue_edit_cannot_overwrite_someone_else(self):
        old=self.state()
        self.command('next')
        with self.assertRaises(JamConflict):
            self.rooms.transact('room','queue',dict(command_id='stale',revision=old['revision'],tracks=self.songs()))

    def test_concurrent_next_commands_with_same_revision_accept_only_one(self):
        revision=self.state()['revision']
        def send(index):
            try:
                self.rooms.transact('room','next',dict(command_id=str(index),revision=revision))
                return True
            except JamConflict:
                return False
        with ThreadPoolExecutor(max_workers=4) as pool:
            self.assertEqual(sum(pool.map(send,range(4))),1)
        self.assertEqual(self.state()['queue_index'],1)

    def test_repeat_all_handles_long_disconnect(self):
        self.command('repeat')
        self.now += 122
        state=self.state()
        self.assertEqual((state['queue_index'],state['position_ms']), (0,2000))

    def test_repeat_one_keeps_track_without_busy_loop(self):
        self.command('repeat');self.command('repeat')
        self.now += 103
        self.assertEqual((self.state()['queue_index'],self.state()['position_ms']), (0,3000))

    def test_reorder_preserves_current_entry_and_position(self):
        current=self.state()['entry_id']
        self.command('reorder', **{'from':0,'to':2})
        state=self.state()
        self.assertEqual(state['entry_id'],current)
        self.assertEqual(state['queue_index'],2)

    def test_remove_uses_entry_id_not_ambiguous_video_id(self):
        state=self.command('append',tracks=[self.songs()[1]])
        duplicate=state['queue'][-1]['entry_id']
        self.command('remove',entry_id=duplicate)
        self.assertEqual(len(self.state()['queue']),3)

    def test_current_song_cannot_be_removed(self):
        with self.assertRaises(JamConflict): self.command('remove',entry_id=self.state()['entry_id'])

    def test_single_output_rejects_multiple_devices(self):
        with self.assertRaises(ValueError): self.command('configure',mode='single',selected=['a','b'])

    def test_multiple_outputs_use_identical_queue_and_timeline(self):
        state=self.command('configure',mode='multiple',selected=['a','b'])
        self.assertEqual(state['selected'],['a','b'])
        self.assertEqual(state['video_id'],'aaaaaaaaaaa')

    def test_missing_duration_can_be_filled_without_changing_entry_identity(self):
        state=self.command('queue',tracks=[dict(video_id='aaaaaaaaaaa',title='Unknown')])
        entry=state['entry_id']
        self.command('duration',entry_id=entry,duration_ms=12000)
        self.now += 13
        self.assertFalse(self.state()['playing'])
        self.assertEqual(self.state()['entry_id'],entry)

    def test_invalid_duration_and_non_catalog_songs_rejected(self):
        with self.assertRaises(ValueError):self.command('queue',tracks=[{'video_id':'file:///secret'}])
        with self.assertRaises(ValueError):self.command('duration',entry_id=self.state()['entry_id'],duration_ms=True)

    def test_state_survives_server_restart(self):
        self.command('pause')
        restored=JamRooms(self.database,lambda:self.now)
        self.assertFalse(restored.transact('room')['playing'])

    def test_guest_id_cannot_be_used_by_signed_in_account(self):
        member=self.members.join('room',None,'Guest','guest-device')
        self.assertIsNone(self.members.get(member,'room','alice'))

    def test_signed_in_member_credentials_cannot_be_swapped_between_accounts(self):
        member=self.members.join('room','alice','Alice','alice-phone')
        self.assertIsNone(self.members.get(member,'room','bob'))

    def test_host_removal_revokes_all_sessions_and_blocks_same_account_rejoin(self):
        first=self.members.join('room','alice','Alice','phone')
        second=self.members.join('room','alice','Alice','tablet')
        self.assertTrue(self.members.remove('room',first))
        self.assertIsNone(self.members.get(second,'room','alice'))
        with self.assertRaises(PermissionError):self.members.join('room','alice','Alice','new-phone')
        with self.assertRaises(PermissionError):self.rooms.transact('room','pause',{'command_id':'revoked'},member=second)

    def test_removed_guest_cookie_cannot_rejoin_same_room(self):
        member=self.members.join('room',None,'Guest')
        self.members.remove('room',member)
        with self.assertRaises(PermissionError):self.members.join('room',None,'Guest',previous=member)

    def test_voluntary_leave_allows_explicit_rejoin(self):
        member=self.members.join('room','alice','Alice')
        self.members.leave(member)
        self.assertIsNone(self.members.get(member,'room','alice'))
        self.assertEqual(self.members.join('room','alice','Alice',previous=member),member)

    def test_removed_or_departed_outputs_are_not_exposed(self):
        member=self.members.join('room','alice','Alice','phone')
        self.rooms.presence('room','phone',member,'Alice phone')
        self.members.remove('room',member)
        self.assertEqual(self.rooms.outputs('room'),[])

    def test_output_presence_cannot_impersonate_another_member(self):
        self.rooms.presence('room','phone','alice','Phone')
        with self.assertRaises(PermissionError):self.rooms.presence('room','phone','bob','Other')

    def test_ending_room_rejects_inflight_command_even_with_previously_valid_member(self):
        member=self.members.join('room','alice','Alice')
        with self.database() as db:
            db.execute("DELETE FROM jam_tokens WHERE token='room'");db.commit()
        with self.assertRaises(PermissionError):self.rooms.transact('room','pause',{'command_id':'late'},member=member)


if __name__ == '__main__': unittest.main()
