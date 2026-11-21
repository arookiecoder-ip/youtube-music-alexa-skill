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

    def test_invite_attaches_guest_without_restarting_host(self):
        self.now += 3
        before = self.state()
        member = self.members.join('room', 'alice', 'Alice', 'alice-phone')
        self.rooms.presence('room', 'alice-phone', member, 'Alice phone')
        self.assertTrue(self.rooms.attach_phone('room', member, 'alice-phone'))
        after = self.state()
        for field in ('queue', 'entry_id', 'position_ms', 'playing', 'transport_revision', 'seek_revision', 'queue_version'):
            self.assertEqual(after[field], before[field], field)
        self.assertEqual(after['mode'], 'multiple')
        self.assertEqual(after['selected'], ['host-phone', 'alice-phone'])
        revision = after['revision']
        self.assertTrue(self.rooms.attach_phone('room', member, 'alice-phone'))
        self.assertEqual(self.state()['revision'], revision)

    def test_invite_cannot_attach_another_members_device(self):
        member = self.members.join('room', 'alice', 'Alice', 'alice-phone')
        with self.assertRaises(PermissionError):
            self.rooms.attach_phone('room', member, 'host-phone')

    def test_invite_does_not_mix_phone_with_echo(self):
        self.command('configure', mode='single', selected=['host-phone'], targets={'host-phone':'alexa:echo'})
        member = self.members.join('room', 'alice', 'Alice', 'alice-phone')
        before = self.state()
        self.assertFalse(self.rooms.attach_phone('room', member, 'alice-phone'))
        self.assertEqual(self.state()['selected'], before['selected'])

    def test_radio_keeps_current_song_identity_and_transport(self):
        self.now += 3
        before = self.state()
        after = self.command('radio')
        for field in ('entry_id', 'position_ms', 'playing', 'transport_revision', 'seek_revision'):
            self.assertEqual(after[field], before[field], field)
        self.assertEqual(len(after['queue']), 1)
        self.assertGreater(after['queue_version'], before['queue_version'])

    def test_radio_new_seed_replaces_song_and_starts_it(self):
        after = self.command('radio', tracks=[self.songs()[1]])
        self.assertEqual(after['video_id'], 'bbbbbbbbbbb')
        self.assertEqual(after['position_ms'], 0)
        self.assertTrue(after['playing'])
        self.assertEqual(len(after['queue']), 1)

    def test_queue_artwork_normalizes_catalog_shapes_and_falls_back(self):
        for fields, expected in [
            ({'thumbnail': {'url':'https://example.test/art.jpg'}}, 'https://example.test/art.jpg'),
            ({'thumbnails':[{'url':'https://example.test/small.jpg'}, {'url':'https://example.test/large.jpg'}]}, 'https://example.test/large.jpg'),
            ({'thumbnail': 'invalid'}, 'https://i.ytimg.com/vi/aaaaaaaaaaa/hqdefault.jpg'),
            ({}, 'https://i.ytimg.com/vi/aaaaaaaaaaa/hqdefault.jpg'),
        ]:
            self.assertEqual(self.rooms.tracks([dict(self.songs()[0], **fields)])[0]['thumbnail'], expected)

    def test_start_preserves_existing_host_media_identity_and_position(self):
        self.rooms.start('room',{'queue':[dict(self.songs()[0],entry_id='already-playing')],
            'queue_index':0,'playing':True,'position_ms':4500},'host-phone')
        state=self.state()
        self.assertEqual(state['queue'][0]['entry_id'],'already-playing')
        self.assertEqual(state['position_ms'],4500)
        self.assertTrue(state['playing'])

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

    def test_backwards_wall_clock_adjustment_does_not_freeze_playback(self):
        self.now -= 10
        self.assertEqual(self.state()['position_ms'],0)
        self.now += 2
        self.assertEqual(self.state()['position_ms'],2000)

    def test_repeat_all_manual_previous_and_next_wrap_at_boundaries(self):
        self.command('repeat')
        self.assertEqual(self.command('previous')['queue_index'],2)
        self.assertEqual(self.command('next')['queue_index'],0)

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

    def test_same_device_recovers_membership_without_cookie(self):
        member = self.members.join('room', 'alice', 'Alice', 'phone')
        self.rooms.presence('room', 'phone', member, 'Phone')
        recovered = self.members.join('room', 'alice', 'Alice', 'phone')
        self.assertEqual(recovered, member)
        self.rooms.presence('room', 'phone', recovered, 'Phone')
        self.members.leave(member)
        self.assertEqual(self.members.join('room', 'alice', 'Alice', 'phone'), member)
        self.assertIsNotNone(self.members.get(member, 'room', 'alice'))

    def test_recovery_uses_original_member_even_with_duplicate_cookie(self):
        member = self.members.join('room', 'alice', 'Alice', 'phone')
        with self.database() as db:
            db.execute("INSERT INTO jam_members(id,token,user_id,name,device_id,created_at) "
                       "VALUES('duplicate','room','alice','Alice','phone',999999999999)")
            db.commit()
        self.assertEqual(self.members.join('room', 'alice', 'Alice', 'phone', 'duplicate'), member)

    def test_presence_recovers_existing_duplicate_but_rejects_other_accounts(self):
        original = self.members.join('room', 'alice', 'Alice', 'phone')
        self.rooms.presence('room', 'phone', original, 'Phone')
        with self.database() as db:
            db.execute("INSERT INTO jam_members(id,token,user_id,name,device_id,created_at) "
                       "VALUES('duplicate','room','alice','Alice','phone',999999999999)")
            db.commit()
        self.rooms.presence('room', 'phone', 'duplicate', 'Phone')
        self.rooms.presence('room', 'phone', original, 'Phone')
        other = self.members.join('room', 'bob', 'Bob', 'phone')
        with self.assertRaises(PermissionError):
            self.rooms.presence('room', 'phone', other, 'Phone')
        self.members.leave('duplicate')
        with self.assertRaises(PermissionError):
            self.rooms.presence('room', 'phone', 'duplicate', 'Phone')

    def test_concurrent_same_device_joins_share_membership(self):
        with ThreadPoolExecutor(max_workers=6) as workers:
            members = list(workers.map(lambda _: self.members.join('room', 'alice', 'Alice', 'phone'), range(12)))
        self.assertEqual(len(set(members)), 1)

    def test_recovery_never_reuses_another_identity_or_device(self):
        member = self.members.join('room', 'alice', 'Alice', 'phone')
        self.assertNotEqual(self.members.join('room', 'bob', 'Bob', 'phone'), member)
        self.assertNotEqual(self.members.join('room', 'alice', 'Alice', 'tablet'), member)
        self.members.remove('room', member)
        with self.assertRaises(PermissionError):
            self.members.join('room', 'alice', 'Alice', 'phone')

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


    def test_reported_buffering_freezes_room_and_does_not_skip_songs(self):
        self.rooms.presence('room','host-phone',None,'Echo',status={'video_id':'aaaaaaaaaaa','playing':False,'confirmed':False,'processing':True,'position_ms':1000})
        state=self.rooms.presented('room',self.state())
        self.assertFalse(state['playback_confirmed']);self.assertTrue(state['playback_processing'])
        for _ in range(10):
            self.now+=4
            self.rooms.presence('room','host-phone',None,'Echo',status={'video_id':'aaaaaaaaaaa','playing':False,'confirmed':False,'processing':True,'position_ms':1000})
            state=self.rooms.presented('room',self.state())
            self.assertEqual(state['queue_index'],0);self.assertEqual(state['position_ms'],1000)
        self.rooms.presence('room','host-phone',None,'Echo',status={'video_id':'aaaaaaaaaaa','playing':True,'confirmed':True,'processing':False,'position_ms':2000})
        state=self.rooms.presented('room',self.state())
        self.assertTrue(state['playback_confirmed']);self.assertFalse(state['playback_processing'])
        self.assertEqual(state['position_ms'],2000)

    def test_volume_is_per_selected_device_without_modifying_shared_queue(self):
        member=self.members.join('room','alice','Alice','alice-phone')
        for device,owner in [('host-phone',None),('alice-phone',member)]:
            self.rooms.presence('room',device,owner,device,status={'volume':25})
        state=self.command('configure',mode='multiple',selected=['host-phone','alice-phone'])
        self.rooms.volume('room','host-phone',80,member)
        self.assertEqual(self.state()['revision'],state['revision'])
        self.assertEqual(self.rooms.device_commands('room','alice-phone',member),[])
        commands=self.rooms.device_commands('room','host-phone',None)
        self.assertEqual(commands[0]['value'],80)
        self.assertEqual(self.rooms.device_commands('room','host-phone',member),[])
        self.rooms.volume('room','host-phone',65,member)
        commands=self.rooms.device_commands('room','host-phone',None)
        self.assertEqual(len(commands),1);self.assertEqual(commands[0]['value'],65)
        self.rooms.presence('room','host-phone',None,'Echo',ack=[commands[0]['id']])
        self.assertEqual(self.rooms.device_commands('room','host-phone',None),[])
        self.now+=21
        with self.assertRaises(JamConflict):self.rooms.volume('room','host-phone',5,member)

    def test_stale_or_wrong_song_report_never_confirms_room_audio(self):
        for report in [{'video_id':'bbbbbbbbbbb','playing':True,'confirmed':True},
                       {'video_id':'aaaaaaaaaaa','playing':True,'confirmed':False}]:
            self.rooms.presence('room','host-phone',None,'Echo',status=report)
            self.assertFalse(self.rooms.presented('room',self.state())['playback_confirmed'])
        self.rooms.presence('room','host-phone',None,'Echo',status={'video_id':'aaaaaaaaaaa','playing':True,'confirmed':True})
        self.now+=9
        self.assertFalse(self.rooms.presented('room',self.state())['playback_confirmed'])

    def test_removed_member_cannot_queue_volume_on_host(self):
        member=self.members.join('room','alice','Alice','alice-phone')
        self.rooms.presence('room','host-phone',None,'Echo')
        self.members.remove('room',member)
        with self.assertRaises(PermissionError):self.rooms.volume('room','host-phone',80,member)


    def test_shuffle_state_is_shared_and_toggling_off_restores_upcoming_order(self):
        original=self.state();ids=[row['entry_id'] for row in original['queue']]
        shuffled=self.command('shuffle')
        self.assertTrue(shuffled['shuffle']);self.assertEqual(shuffled['entry_id'],original['entry_id'])
        restored=self.command('shuffle')
        self.assertFalse(restored['shuffle']);self.assertEqual([row['entry_id'] for row in restored['queue']],ids)


if __name__ == '__main__': unittest.main()
