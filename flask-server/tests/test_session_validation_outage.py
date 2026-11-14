"""A transient account DB failure must be 503, never a false expired-login 401."""
import ast
from pathlib import Path
from types import SimpleNamespace
import sqlite3
import unittest
from unittest.mock import Mock
from flask import Flask,session,g,has_request_context

class SessionValidationOutageTests(unittest.TestCase):
    def test_temporary_validation_failure_retains_cookie_and_never_reaches_owner_fallback(self):
        app=Flask(__name__);app.secret_key='isolated-outage-test'
        accounts=SimpleNamespace(validate_session=Mock(side_effect=sqlite3.OperationalError('busy')))
        namespace=dict(_multiuser_layer=SimpleNamespace(accounts=accounts),session=session,g=g,
            has_request_context=has_request_context,logger=Mock())
        tree=ast.parse((Path(__file__).parents[1]/'server.py').read_text())
        node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_mu_web_identity')
        exec(compile(ast.Module(body=[node],type_ignores=[]),'session_validation','exec'),namespace)
        app.before_request(namespace['_mu_web_identity'])
        app.add_url_rule('/api/account',view_func=lambda:('must not fall through',200))
        client=app.test_client()
        with client.session_transaction() as cookie:cookie['mu_sid']='still-valid-session'
        self.assertEqual(client.get('/api/account').status_code,503)
        with client.session_transaction() as cookie:self.assertEqual(cookie['mu_sid'],'still-valid-session')
