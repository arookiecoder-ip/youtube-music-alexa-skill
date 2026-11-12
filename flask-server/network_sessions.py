"""Route AlexaPy through private egress and preserve browser sign-in redirects."""
import asyncio
import os
from aiohttp import ClientSession
from alexapy import AlexaLogin
from urllib.parse import urljoin, urlsplit


class _LoginPostRequest:
    """Preserve aiohttp's awaitable/context-manager request interface."""
    def __init__(self, coroutine):
        self.coroutine = coroutine
        self.response = None

    def __await__(self):
        return self.coroutine.__await__()

    async def __aenter__(self):
        self.response = await self.coroutine
        return await self.response.__aenter__()

    async def __aexit__(self, *args):
        return await self.response.__aexit__(*args)


class _AmazonLoginSession:
    """Follow manual sign-in POST redirects with browser HTTP semantics.

    AlexaPy otherwise reposts credentials on 301/302, losing the MFA page.
    Non-sign-in requests delegate unchanged to the real aiohttp session.
    """
    def __init__(self, session, domain):
        self._session = session
        domain = str(domain).strip().lower()
        self._hosts = {'amazon.com', 'www.amazon.com', domain, 'www.' + domain}

    def __getattr__(self, name):
        return getattr(self._session, name)

    def _safe_url(self, url):
        try:
            parsed = urlsplit(str(url))
            return (parsed.scheme == 'https' and parsed.hostname in self._hosts
                    and parsed.port in (None, 443) and not parsed.username and not parsed.password)
        except ValueError:
            return False

    def post(self, url, **kwargs):
        if kwargs.get('allow_redirects') is False and urlsplit(str(url)).path.startswith(('/ap/', '/ax/')):
            return _LoginPostRequest(self._sign_in_post(str(url), kwargs))
        return self._session.post(url, **kwargs)

    async def _sign_in_post(self, url, kwargs):
        method = 'post'
        kwargs = dict(kwargs, allow_redirects=False)
        if not self._safe_url(url):
            raise ValueError('Unsafe Amazon sign-in destination.')
        for hop in range(6):
            response = await getattr(self._session, method)(url, **kwargs)
            location = response.headers.get('Location')
            if response.status not in (301, 302, 303, 307, 308) or not location:
                return response
            try:
                target = urljoin(str(response.url), location)
                if not self._safe_url(target):
                    raise ValueError('Unsafe Amazon sign-in redirect.')
                if response.status in (307, 308) and method == 'post':
                    if urlsplit(target).netloc != urlsplit(url).netloc:
                        raise ValueError('Cross-origin Amazon credential redirect refused.')
                elif response.status in (301, 302, 303):
                    method = 'get'
                    for field in ('data', 'json', 'params'):
                        kwargs.pop(field, None)
                    kwargs['headers'] = {k: v for k, v in (kwargs.get('headers') or {}).items()
                                         if k.lower() not in ('content-type', 'content-length', 'authorization')}
                if hop == 5:
                    raise ValueError('Too many Amazon sign-in redirects.')
            finally:
                response.release()
            url = target


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
        if not isinstance(self._session, _AmazonLoginSession):
            self._session = _AmazonLoginSession(self._session, self.url)

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
        self._resp_status = resp.status
        return url

    async def _process_page(self, html, site):
        self._page_url = getattr(self, "_resp_url", None) or str(site)
        self._page_status = getattr(self, '_resp_status', None)
        self._page_html = html
        return await super()._process_page(html, site)
