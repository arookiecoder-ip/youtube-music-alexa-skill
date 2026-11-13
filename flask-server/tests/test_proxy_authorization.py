"""Exercise proxy guards without loading provider clients or real credentials."""
import ast
import asyncio
import hmac
import os
from pathlib import Path
import threading
import time
from types import SimpleNamespace
import unittest
from unittest import mock
from aiohttp import web
from multidict import CIMultiDict


class ProxyAuthorizationTests(unittest.TestCase):
    def setUp(self):
        tree = ast.parse((Path(__file__).parents[1] / 'alexa_remote.py').read_text())
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'AlexaRemote')
        methods = [n for n in cls.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in ('proxy_authorization', '_proxy_handler')]
        self.ns = dict(_proxy_guard=threading.Lock(), _proxy_holder=[None], _proxy_claim_expires=[time.monotonic()+60], time=time, os=os, hmac=hmac, web=web, asyncio=asyncio)
        exec(compile(ast.Module(body=methods, type_ignores=[]), '<proxy-guards>', 'exec'), self.ns)
        self.remote = SimpleNamespace(_proxy_authorizer=lambda cookie,attempt: True, _proxy_attempt_started=time.monotonic(), _proxy_started=True, _proxy_attempt_id='private-attempt', _proxy=SimpleNamespace(all_handler=mock.AsyncMock(return_value=web.Response())), _proxy_login=None)
        self.remote.proxy_authorization = lambda: self.ns['proxy_authorization'](self.remote)
        self.ns['_proxy_holder'][0] = self.remote

    def test_only_current_holder_has_an_unexpired_credential(self):
        self.assertEqual(self.remote.proxy_authorization(), 'private-attempt')
        self.ns['_proxy_holder'][0] = object()
        self.assertIsNone(self.remote.proxy_authorization())
        self.ns['_proxy_holder'][0] = self.remote
        self.ns['_proxy_claim_expires'][0] = time.monotonic()-1
        self.assertIsNone(self.remote.proxy_authorization())

    def test_direct_requests_cannot_bypass_caddy(self):
        with mock.patch.dict(os.environ, ALEXA_PROXY_BEHIND_CADDY='1'):
            for value in ('', 'wrong', 'old-attempt'):
                request = SimpleNamespace(headers={'X-MusicBox-Proxy-Authorization':value})
                with self.assertRaises(web.HTTPForbidden):
                    asyncio.run(self.ns['_proxy_handler'](self.remote, request))
        self.remote._proxy.all_handler.assert_not_awaited()

    def test_internal_header_is_stripped_before_provider_receives_request(self):
        request = mock.Mock(headers=CIMultiDict({'X-MusicBox-Proxy-Authorization':'private-attempt','Cookie':'browser-cookie'}), method='POST')
        request.clone.return_value = SimpleNamespace(method='POST')
        with mock.patch.dict(os.environ, ALEXA_PROXY_BEHIND_CADDY='1'):
            asyncio.run(self.ns['_proxy_handler'](self.remote, request))
        request.clone.assert_called_once_with(headers={'Cookie':'browser-cookie'})
        self.remote._proxy.all_handler.assert_awaited_once_with(request.clone.return_value)
