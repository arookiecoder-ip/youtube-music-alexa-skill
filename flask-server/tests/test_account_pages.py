"""Real account routes: owner access, lease fencing and no credential caching."""
import ast
from pathlib import Path
from unittest.mock import Mock
from types import SimpleNamespace
import unittest
from flask import Flask, request, jsonify, redirect, render_template, abort

class AccountPageTests(unittest.TestCase):
    def setUp(self):
        root=Path(__file__).resolve().parents[1]
        app=Flask(__name__,template_folder=str(root/'templates'))
        self.logged=True;self.allowed=True
        self.controller=Mock();self.controller.call.return_value={'width':480,'height':800,'image':'data:image/jpeg;base64,AA=='}
        def no_store(r):r.headers['Cache-Control']='no-store';return r
        namespace=dict(_STATIC_VERSION="fixture-assets",app=app,request=request,jsonify=jsonify,redirect=redirect,render_template=render_template,abort=abort,
            _logged_in=lambda:self.logged,_browser_owner_id=lambda:'owner',_no_store=no_store,
            error_response=lambda message,status:(jsonify(error=message),status),
            _youtube_browser_sessions=SimpleNamespace(COOKIE_NAME='lease',authorize=lambda owner,token:self.allowed and token=='valid',controller=self.controller))
        nodes=[n for n in ast.parse((root/'server.py').read_text()).body if isinstance(n,ast.FunctionDef) and n.name in ['account_signin_page','youtube_browser_surface']]
        exec(compile(ast.Module(body=nodes,type_ignores=[]),str(root/'server.py'),'exec'),namespace)
        self.client=app.test_client()
    def test_pages_are_owner_only(self):
        self.logged=False
        for provider in ['amazon','youtube']:
            self.assertEqual(self.client.get('/accounts/'+provider).status_code,302)
    def test_responsive_pages_are_not_cached_or_vnc(self):
        for provider in ['amazon','youtube']:
            r=self.client.get('/accounts/'+provider);self.assertEqual(r.status_code,200)
            self.assertEqual(r.headers['Cache-Control'],'no-store');self.assertIn(b'width=device-width',r.data)
            self.assertNotIn(b'vnc.html',r.data)
            self.assertIn(b'signin.css?v=fixture-assets',r.data)
            self.assertIn(b'account-signin.js?v=fixture-assets',r.data)
        self.assertEqual(self.client.get('/accounts/unknown').status_code,404)
    def test_surface_requires_owner_and_current_lease(self):
        self.assertEqual(self.client.get('/api/youtube/browser-session/surface').status_code,403)
        self.client.set_cookie('lease','valid');self.allowed=False
        self.assertEqual(self.client.post('/api/youtube/browser-session/surface',json={}).status_code,403)
        self.allowed=True;self.logged=False
        self.assertEqual(self.client.get('/api/youtube/browser-session/surface').status_code,401)
        self.controller.call.assert_not_called()
    def test_surface_does_not_cache_and_forwards_bounded_input_to_sidecar(self):
        self.client.set_cookie('lease','valid')
        r=self.client.get('/api/youtube/browser-session/surface');self.assertEqual(r.status_code,200);self.assertEqual(r.headers['Cache-Control'],'no-store')
        payload={'type':'key','key':'Enter'}
        self.assertEqual(self.client.post('/api/youtube/browser-session/surface',json=payload).status_code,200)
        self.controller.call.assert_called_with('/interactive/input','POST',payload=payload)
    def test_surface_failure_is_recoverable(self):
        self.client.set_cookie('lease','valid');self.controller.call.side_effect=TimeoutError()
        self.assertEqual(self.client.get('/api/youtube/browser-session/surface').status_code,503)
