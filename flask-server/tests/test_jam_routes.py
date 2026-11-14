"""Run production Jam routes and authorization hooks without booting providers."""
import ast
import json
import contextvars
import secrets
import threading
from pathlib import Path
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
from flask import Flask, Response, request, session, jsonify, render_template

import test_jam_rooms as room_tests
from jam_rooms import JamConflict


class JamRouteTests(unittest.TestCase):
    songs = staticmethod(room_tests.JamRoomsTests.songs)

    def setUp(self):
        room_tests.JamRoomsTests.setUp(self)
        with self.database() as db:
            db.execute('UPDATE jam_tokens SET created_at=?',(time.time(),));db.commit()
        root=Path(__file__).resolve().parents[1]
        app=Flask(__name__,template_folder=str(root/'templates'),static_folder=str(root/'templates'/'static'))
        app.secret_key='isolated-jam-tests'
        app.config['TESTING']=True
        self.app=app
        cache={'room':{'created_at':time.time(),'user_id':'host'}}
        identity=contextvars.ContextVar('identity',default='owner')
        layer=SimpleNamespace(alexa_identity=SimpleNamespace(skill_namespace=lambda _:''),
            alexa_access=SimpleNamespace(enabled=lambda _:True))
        self.ns=dict(_jam_drive=lambda token:None,json=json,app=app,Response=Response,request=request,session=session,jsonify=jsonify,render_template=render_template,
            time=time,secrets=secrets,get_db=self.database,_jam_members=self.members,_jam_rooms=self.rooms,JamConflict=JamConflict,
            _jam_cache=lambda:cache,JAM_MAX_AGE=86400,
            _mu_web_identity=lambda:dict(id=request.headers['Test-User'],username=request.headers['Test-User'],role='user') if request.headers.get('Test-User') else None,
            _logged_in=lambda:bool(request.headers.get('Test-User')),
            _valid_key_supplied=lambda:request.headers.get('X-Api-Key')=='secret',
            _no_store=lambda response:response,_JAM_ENDED_HTML='<h1>Jam ended</h1>',
            _jam_url=lambda token:'https://example.test/j/'+token,
            _multiuser_layer=layer,_PLAYER_IDENTITY=identity,
            _player_identity_for_user_id=lambda user,role=None:'user:'+str(user),
            _ensure_db=lambda:None,_is_spa_document_path=lambda _:False,
            _PUBLIC_PATHS=(),_PUBLIC_PREFIXES=('/j/','/jam/','/jam-invite/'),
            _is_web_session_path=lambda path:path.startswith(('/api/','/alexa/')),
            _is_api_path=lambda path:path.startswith(('/api/','/alexa/')),
            password_change_allowed=lambda *_:True,API_KEY='secret',
            _remote_login_enabled=lambda:True,_alexa_auth_error=Mock(),
            _client_ip=lambda:'192.0.2.1',_rate_limiter=Mock(),logger=Mock())
        self.ns['_rate_limiter'].check.return_value=(True,0)
        self.ns['_valid_video_id']=lambda video:isinstance(video,str) and len(video)==11
        self.ns['_serve_youtube_stream']=lambda video:Response(b'fixture audio',mimetype='audio/wav')
        names={'_jam_membership','_jam_identity_id','_jam_token_valid','_jam_purge_token','_jam_active_token',
            '_jam_guest','_jam_prune_records','_jam_request_allowed','_bind_player_identity','require_api_key',
            'jam_join','_jam_access_token','jam_state','jam_presence','jam_control','jam_remove_member',
            'jam_guest_leave','android_asset_links','jam_room_page','jam_audio','_rate_limit_group','_check_rate_limit','_jam_detach_account_output'}
        tree=ast.parse((root/'server.py').read_text())
        nodes=[]
        for node in tree.body:
            if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id in ('_JAM_PATHS','_JAM_PUBLIC_GET_PREFIXES','_RATE_LIMITS') for t in node.targets):nodes.append(node)
            if isinstance(node,ast.FunctionDef) and node.name in names:
                node.decorator_list=[];nodes.append(node)
        exec(compile(ast.Module(body=nodes,type_ignores=[]),str(root/'server.py'),'exec'),self.ns)
        app.before_request(self.ns['_bind_player_identity'])
        app.before_request(self.ns['require_api_key'])
        app.before_request(self.ns['_check_rate_limit'])
        routes=[('/j/<token>','jam_join',['GET','POST']),('/api/jam/state/','jam_state',['GET']),
            ('/api/jam/presence/','jam_presence',['POST']),('/api/jam/control/','jam_control',['POST']),
            ('/alexa/jam/remove/','jam_remove_member',['POST']),('/api/jam/leave/','jam_guest_leave',['POST']),
            ('/api/jam/audio/<video_id>','jam_audio',['GET','HEAD']),('/.well-known/assetlinks.json','android_asset_links',['GET']),('/jam-room/','jam_room_page',['GET'])]
        for path,name,methods in routes:app.add_url_rule(path,name,self.ns[name],methods=methods)
        app.add_url_rule('/alexa/like/','like',lambda:jsonify(identity=identity.get()),methods=['POST'])
        app.add_url_rule('/api/library/playlists/mine/tracks','playlist',lambda:jsonify(identity=identity.get()),methods=['POST'])
        app.add_url_rule('/alexa/now_playing/','legacy',lambda:jsonify(identity=identity.get()))
        self.host=app.test_client();self.alice=app.test_client();self.guest=app.test_client()

    def tearDown(self):self.temp.cleanup()

    def join(self,client,user=None,device='phone'):
        return client.post('/j/room',json={'device_id':device},headers={'Test-User':user} if user else {})

    def test_invitation_get_shows_consent_without_joining(self):
        response=self.alice.get('/j/room',headers={'Test-User':'alice'})
        self.assertEqual(response.status_code,200)
        self.assertIn(b'Join Jam',response.data)
        with self.alice.session_transaction() as cookie:self.assertNotIn('jam',cookie)

    def test_signed_in_join_preserves_personal_identity_for_likes_and_playlists(self):
        self.assertEqual(self.join(self.alice,'alice').status_code,200)
        for path in ('/alexa/like/','/api/library/playlists/mine/tracks'):
            response=self.alice.post(path,json={'video_id':'aaaaaaaaaaa'},headers={'Test-User':'alice'})
            self.assertEqual(response.get_json()['identity'],'user:alice')

    def test_guest_cannot_call_private_account_actions_or_legacy_host_playback(self):
        self.join(self.guest)
        for path in ('/alexa/like/','/api/library/playlists/mine/tracks'):
            self.assertEqual(self.guest.post(path,json={}).status_code,401)
        self.assertEqual(self.guest.get('/alexa/now_playing/').status_code,401)

    def test_host_opening_own_invite_does_not_become_member(self):
        self.assertTrue(self.join(self.host,'host').get_json()['host'])
        with self.host.session_transaction() as cookie:self.assertNotIn('jam',cookie)

    def test_invalid_or_ended_invite_is_gone(self):
        self.assertEqual(self.guest.get('/j/missing').status_code,410)

    def test_guest_sees_shared_queue_but_not_member_credentials(self):
        self.join(self.guest)
        data=self.guest.get('/api/jam/state/').get_json()
        self.assertTrue(data['active']);self.assertFalse(data['host'])
        self.assertEqual(data['members'],[])
        self.assertEqual(data['playback']['video_id'],'aaaaaaaaaaa')

    def test_member_can_choose_outputs_but_cannot_remove_users(self):
        self.join(self.alice,'alice')
        headers={'Test-User':'alice'}
        self.assertEqual(self.alice.post('/alexa/jam/remove/',json={'member_id':'x'},headers=headers).status_code,403)
        self.assertEqual(self.alice.post('/api/jam/control/',json={'action':'configure','mode':'single','selected':[],'revision':1,'command_id':'configure'},headers=headers).status_code,200)

    def test_removed_member_cannot_control_and_cannot_rejoin(self):
        self.join(self.alice,'alice')
        data=self.host.get('/api/jam/state/',headers={'Test-User':'host'}).get_json()
        member=data['members'][0]['id']
        self.assertEqual(self.host.post('/alexa/jam/remove/',json={'member_id':member},headers={'Test-User':'host'}).status_code,200)
        self.assertEqual(self.alice.post('/api/jam/control/',json={'action':'pause','command_id':'x'},headers={'Test-User':'alice'}).status_code,403)
        self.assertEqual(self.join(self.alice,'alice').status_code,403)

    def test_json_csrf_fence_rejects_cross_site_form_join_and_commands(self):
        self.assertEqual(self.guest.post('/j/room',data={'device_id':'phone'}).status_code,415)
        self.join(self.guest)
        self.assertEqual(self.guest.post('/api/jam/control/',data={'action':'pause'}).status_code,401)

    def test_malformed_json_object_is_rejected(self):
        self.assertEqual(self.guest.post('/j/room',json=['bad']).status_code,400)
        self.join(self.guest)
        self.assertEqual(self.guest.post('/api/jam/control/',json=['bad']).status_code,400)

    def test_member_cannot_register_a_different_users_device(self):
        self.join(self.alice,'alice',device='alice-phone')
        response=self.alice.post('/api/jam/presence/',json={'device_id':'host-phone'},headers={'Test-User':'alice'})
        self.assertEqual(response.status_code,403)

    def test_host_can_choose_multiple_registered_outputs(self):
        self.join(self.alice,'alice',device='alice-phone')
        self.alice.post('/api/jam/presence/',json={'device_id':'alice-phone','name':'Alice phone'},headers={'Test-User':'alice'})
        self.host.post('/api/jam/presence/',json={'device_id':'host-phone','name':'Host phone'},headers={'Test-User':'host'})
        state=self.host.get('/api/jam/state/',headers={'Test-User':'host'}).get_json()['playback']
        response=self.host.post('/api/jam/control/',json={'action':'configure','mode':'multiple','selected':['host-phone','alice-phone'],'command_id':'mode','revision':state['revision']},headers={'Test-User':'host'})
        self.assertEqual(response.status_code,200)
        self.assertTrue(self.alice.get('/api/jam/state/?device_id=alice-phone',headers={'Test-User':'alice'}).get_json()['listen'])

    def test_revoked_signed_in_member_keeps_personal_account_access(self):
        self.join(self.alice,'alice')
        with self.alice.session_transaction() as cookie:member=cookie['jam_member']
        self.members.remove('room',member)
        self.assertEqual(self.alice.post('/alexa/like/',json={},headers={'Test-User':'alice'}).status_code,200)
        self.assertTrue(self.alice.get('/api/jam/state/',headers={'Test-User':'alice'}).get_json()['revoked'])

    def test_removed_anonymous_guest_can_observe_revocation_and_clear_cookie(self):
        self.join(self.guest)
        with self.guest.session_transaction() as cookie:member=cookie['jam_member']
        self.members.remove('room',member)
        data=self.guest.get('/api/jam/state/').get_json()
        self.assertFalse(data['active']);self.assertTrue(data['revoked'])
        self.assertEqual(self.guest.post('/api/jam/leave/',json={}).status_code,200)
        with self.guest.session_transaction() as cookie:self.assertNotIn('jam',cookie)

    def test_other_workers_token_revocation_is_seen_without_cache_restart(self):
        self.join(self.alice,'alice')
        with self.database() as db:
            db.execute("DELETE FROM jam_tokens WHERE token='room'");db.commit()
        data=self.alice.get('/api/jam/state/',headers={'Test-User':'alice'}).get_json()
        self.assertFalse(data['active']);self.assertTrue(data['revoked'])

    def test_expired_invitation_cannot_be_joined(self):
        with self.database() as db:
            db.execute('UPDATE jam_tokens SET created_at=?',(time.time()-90000,));db.commit()
        self.assertEqual(self.join(self.alice,'alice').status_code,410)

    def test_removed_guest_cannot_bypass_removal_by_clicking_leave_then_reopening_invite(self):
        self.join(self.guest)
        with self.guest.session_transaction() as cookie:member=cookie['jam_member']
        self.members.remove('room',member)
        self.guest.post('/api/jam/leave/',json={})
        self.assertEqual(self.join(self.guest).status_code,403)

    def test_stable_queue_is_omitted_only_when_the_room_epoch_and_version_match(self):
        self.join(self.alice,'alice')
        headers={'Test-User':'alice'}
        first=self.alice.get('/api/jam/state/',headers=headers).get_json()['playback']
        query=f"?queue_epoch={first['queue_epoch']}&queue_version={first['queue_version']}"
        slim=self.alice.get('/api/jam/state/'+query,headers=headers).get_json()['playback']
        self.assertNotIn('queue',slim)
        self.assertEqual(slim['video_id'],first['video_id'])
        other=self.alice.get('/api/jam/state/?queue_epoch=old-room&queue_version=1',headers=headers).get_json()['playback']
        self.assertIn('queue',other)

    def test_room_presence_budget_is_separate_from_personal_api_and_other_nat_participants(self):
        self.join(self.alice,'alice')
        self.alice.get('/api/jam/state/',headers={'Test-User':'alice'})
        first=self.ns['_rate_limiter'].check.call_args.args
        self.assertEqual(first[1],'jam_sync')
        self.alice.post('/alexa/like/',json={},headers={'Test-User':'alice'})
        self.assertEqual(self.ns['_rate_limiter'].check.call_args.args[1],'api')
        self.join(self.guest)
        self.guest.get('/api/jam/state/')
        second=self.ns['_rate_limiter'].check.call_args.args
        self.assertEqual(second[1],'jam_sync')
        self.assertNotEqual(first[0],second[0])

    def test_logout_detaches_only_the_acting_accounts_phone_output(self):
        close=Mock()
        state={'playing':True}
        self.ns.update(_playback_output=SimpleNamespace(snapshot=lambda:dict(playback_output='phone',output_owner='alice-phone'),close_phone=close),
            _np_lock=threading.Lock(),_now_playing=state,_reset_progress=Mock(),_computed_position_ms=lambda:5000,_notify_sse=Mock())
        self.ns['_jam_detach_account_output']()
        close.assert_called_once_with('alice-phone')
        self.assertFalse(state['playing'])

    def test_browser_audio_is_limited_to_room_tracks_and_live_membership(self):
        self.join(self.guest)
        self.assertEqual(self.guest.get('/api/jam/audio/aaaaaaaaaaa').status_code,200)
        self.assertEqual(self.guest.get('/api/jam/audio/zzzzzzzzzzz').status_code,403)
        with self.guest.session_transaction() as cookie:member=cookie['jam_member']
        self.members.remove('room',member)
        self.assertNotEqual(self.guest.get('/api/jam/audio/aaaaaaaaaaa').status_code,200)

    def test_public_app_association_uses_expected_package_and_certificate(self):
        data=self.guest.get('/.well-known/assetlinks.json').get_json()[0]['target']
        self.assertEqual(data['package_name'],'in.synthora.musicbox')
        self.assertEqual(len(data['sha256_cert_fingerprints'][0].split(':')),32)


if __name__ == '__main__':unittest.main()
