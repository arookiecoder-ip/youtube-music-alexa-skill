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

    async def restore_cookie_session(self):
        """Validate persisted credentials without deleting them on a failed probe.

        AlexaPy login falls back to reset() after a failed token/cookie probe.
        That reset deletes persisted cookies even for transient network failures.
        Only interactive reset/logout may discard the saved session.
        """
        cookies = await self.load_cookie()
        if not cookies:
            return False
        self._restoring_saved_session = True
        try:
            await self.login(cookies=cookies)
            return bool((self.status or {}).get("login_successful"))
        finally:
            self._restoring_saved_session = False

    async def reset(self):
        if not getattr(self, '_restoring_saved_session', False):
            return await super().reset()
        files = self._cookiefile
        self._cookiefile = []
        try:
            await super().reset()
        finally:
            self._cookiefile = files

    # AlexaPy decides the next step from the markup of each answer, then drops
    # the response when a failed confirmation resets the session. Keeping the
    # last page -- url and body captured together -- lets the caller name the
    # page it could not answer; nothing here is logged or persisted.
    async def _process_resp(self, resp):
        url = await super()._process_resp(resp)
        self._resp_url = str(resp.url)
        return url

    async def _process_page(self, html, site):
        self._page_url = getattr(self, "_resp_url", None) or str(site)
        self._page_html = html
        return await super()._process_page(html, site)
