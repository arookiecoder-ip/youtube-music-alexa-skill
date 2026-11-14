"""Real SQLite dispatch fences and independent physical Jam output identities."""
import json
from unittest.mock import Mock
from test_jam_rooms import JamRoomsTests
from jam_echo import JamEchoDriver


class JamEchoTests(JamRoomsTests):
    def register(self, agent='host-phone', member=None, identity='user:host', active=True):
        self.rooms.presence('room',agent,member,'Host device',details=dict(identity=identity,username='Host',kind='alexa' if active else 'phone',devices=[
            dict(id='phone',kind='phone',name='Phone',active=not active,online=True),
            dict(id='alexa:serial',kind='alexa',name='Living room Echo',active=active,online=True)]))
        return next(o for o in self.rooms.outputs('room') if json.loads(o['details']).get('server_echo'))

    def test_phone_and_echo_are_individually_selectable_at_the_same_time(self):
        echo=self.register()
        self.assertEqual(self.state()['selected'],[echo['device']])
        state=self.command('configure',mode='multiple',selected=['host-phone',echo['device']])
        self.assertEqual(len(state['selected']),2)
        root=next(o for o in self.rooms.outputs('room') if o['device']=='host-phone')
        self.assertEqual([d['kind'] for d in json.loads(root['details'])['devices']],['phone'])

    def test_duplicate_apps_do_not_duplicate_the_same_account_echo(self):
        self.register()
        self.register('tablet',active=False)
        outputs=self.rooms.outputs('room')
        self.assertEqual(len([o for o in outputs if json.loads(o['details']).get('server_echo')]),1)
        self.assertEqual(len(outputs),3)

    def test_starting_jam_adopts_an_already_playing_echo_without_restarting_it(self):
        echo=self.register();state=self.state();send=Mock()
        snapshot=Mock(return_value=dict(video_id=state['video_id'],playing=True,confirmed=True,processing=False,position_ms=state['position_ms'],seek_revision=0))
        driver=JamEchoDriver(self.rooms,send,snapshot)
        driver._dispatch('room',echo,state)
        send.assert_not_called()
        self.assertTrue(self.rooms.presented('room',self.state())['playback_confirmed'])

    def test_new_song_is_dispatched_once_and_stale_confirmation_is_fenced(self):
        echo=self.register();send=Mock(return_value=None);driver=JamEchoDriver(self.rooms,send,Mock(return_value=None))
        state=self.state();driver._dispatch('room',echo,state);driver._dispatch('room',echo,state)
        self.assertEqual(send.call_count,1)
        old_intent=None
        with self.database() as db:old_intent=db.execute('SELECT intent FROM jam_echo_intents').fetchone()['intent']
        next_state=self.command('next');driver._dispatch('room',echo,next_state)
        self.assertEqual(send.call_count,2)
        self.assertFalse(self.rooms.echo_report('room',echo['device'],old_intent,dict(confirmed=True,playing=True)))

    def test_echo_failure_backoff_is_not_reset_by_inventory_heartbeats(self):
        echo=self.register();send=Mock(return_value='Amazon temporarily busy')
        driver=JamEchoDriver(self.rooms,send,Mock(return_value=None));state=self.state()
        driver._dispatch('room',echo,state)
        for _ in range(5):
            self.now+=2;echo=self.register();driver._dispatch('room',echo,state)
        self.assertEqual(send.call_count,1)
        self.now+=6;echo=self.register();driver._dispatch('room',echo,state)
        self.assertEqual(send.call_count,2)
