"""The public welcome must not intercept signed-in, API-key or Jam playback."""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from flask import Flask, render_template, session

ROOT=Path(__file__).resolve().parents[1]


class LandingRouteTests(unittest.TestCase):
    def setUp(self):
        app=Flask(__name__,template_folder=str(ROOT/'templates'))
        app.secret_key='landing-test'
        self.logged=False; self.key=False; self.guest=False
        scope=dict(app=app,session=session,render_template=render_template,_STATIC_VERSION='test',
                   _multiuser_layer=SimpleNamespace(config=SimpleNamespace(base_path='/admin')),
                   _logged_in=lambda:self.logged,_valid_key_supplied=lambda:self.key,
                   _jam_guest=lambda:self.guest,_render_root_shell=lambda:'player',
                   _no_store=lambda response:response)
        nodes=[node for node in ast.parse((ROOT/'server.py').read_text()).body if isinstance(node,ast.FunctionDef) and node.name in ('root','welcome')]
        exec(compile(ast.Module(body=nodes,type_ignores=[]),'server.py','exec'),scope)
        self.client=app.test_client()

    def test_anonymous_root_shows_both_real_login_destinations(self):
        response=self.client.get('/')
        self.assertEqual(response.status_code,200)
        self.assertIn(b'href="/admin/login/"',response.data)
        self.assertIn(b'href="/login/"',response.data)

    def test_authenticated_visitors_keep_player(self):
        for credential in ('logged','key','guest'):
            setattr(self,credential,True)
            self.assertEqual(self.client.get('/').data,b'player')
            setattr(self,credential,False)

    def test_stale_jam_still_reaches_existing_terminal_handler(self):
        with self.client.session_transaction() as state: state['jam']='stale'
        self.assertEqual(self.client.get('/').data,b'player')

    def test_welcome_is_always_accessible(self):
        self.logged=True
        self.assertIn(b'Make room',self.client.get('/welcome').data)

    def test_management_check_rejects_the_retired_admin_role(self):
        actor={"role":"admin","status":"active"}
        scope={"_mu_web_identity":lambda:actor,"_valid_key_supplied":lambda:False,"_logged_in":lambda:True}
        node=next(n for n in ast.parse((ROOT/'server.py').read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='_admin_logged_in')
        exec(compile(ast.Module(body=[node],type_ignores=[]),'server.py','exec'),scope)
        self.assertFalse(scope['_admin_logged_in']())
        actor['role']='owner'
        self.assertTrue(scope['_admin_logged_in']())
        actor['role']='user'
        self.assertFalse(scope['_admin_logged_in']())
