"""Opt AlexaPy's fresh sessions into the configured private egress gateway."""
import asyncio
import os
from aiohttp import ClientSession
from alexapy import AlexaLogin


class ProxyAwareAlexaLogin(AlexaLogin):
    def _create_session(self, force=False):
        before = getattr(self, '_session', None)
        super()._create_session(force)
        session = self._session
        if session is not before and (os.environ.get('HTTPS_PROXY') or os.environ.get('HTTP_PROXY')):
            # AlexaPy creates its own session with trust_env=False. Replace only
            # a newly created, unused session, before cookies/login are loaded.
            self._session = ClientSession(headers=session.headers, trust_env=True)
            asyncio.get_running_loop().create_task(session.close())
