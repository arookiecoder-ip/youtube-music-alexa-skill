"""Route AlexaPy through private egress and preserve browser sign-in redirects."""
import asyncio
import os
from aiohttp import ClientSession
from alexapy import AlexaLogin
from urllib.parse import urljoin, urlsplit, parse_qs


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
    def __init__(self, session, domain, on_redirect=None):
        self._session = session
        self._on_redirect = on_redirect
        domain = str(domain).strip().lower()
        self._hosts = {'amazon.com', 'www.amazon.com', domain, 'www.' + domain}

    def __getattr__(self, name):
        return getattr(self._session, name)

    @property
    def _connector(self):
        return self._session._connector

    @_connector.setter
    def _connector(self, value):
        self._session._connector = value

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
                if self._on_redirect:
                    self._on_redirect(target)
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
            self._session = _AmazonLoginSession(self._session, self.url, self._capture_oauth_callback)

    def _capture_oauth_callback(self, url):
        """Match AlexaProxy's callback handoff without accepting arbitrary URLs.

        The scripted flow must hand the authorization code to AlexaPy before
        test_loggedin() calls get_tokens(). A web cookie alone does not provide
        the registered Alexa session/cookies used for regional device status.
        """
        try:
            parsed = urlsplit(str(url))
            hosts = {'www.amazon.com', 'alexa.amazon.com',
                     'www.' + self.url.lower(), 'alexa.' + self.url.lower()}
            if (parsed.scheme != 'https' or parsed.hostname not in hosts
                    or parsed.port not in (None, 443) or parsed.username or parsed.password
                    or parsed.path not in ('/ap/maplanding', '/spa/index.html')):
                return False
            query = parse_qs(parsed.query)
        except ValueError:
            return False
        captured = False
        for key, attribute in (('openid.oa2.authorization_code', 'authorization_code'),
                               ('openid.oa2.access_token', 'access_token')):
            values = query.get(key) or []
            if len(values) == 1 and values[0] and len(values[0]) <= 16384:
                setattr(self, attribute, values[0])
                captured = True
        return captured

    async def test_loggedin(self, *args, **kwargs):
        # Capture the same /api/users/me response used by AlexaPy's successful
        # session check, including its amazon.com marketplace fallback.
        # get_authentication() retries only the configured marketplace, which
        # can still return 401 after the fallback has confirmed the login.
        self._confirmed_identity = None
        self._identity_candidate = None
        self._identity_candidate_domain = None
        self._identity_probe_active = True
        try:
            confirmed = await super().test_loggedin(*args, **kwargs)
        finally:
            self._identity_probe_active = False
        candidate = self._identity_candidate
        if confirmed and candidate and candidate['customerId'] == self.customer_id:
            self._confirmed_identity = candidate
            # Keep every later Alexa API call on the marketplace that actually
            # authenticated this session. The constructor's regional default can
            # still redirect devices/control calls to sign-in after a fallback.
            self._url = self._identity_candidate_domain
        return confirmed

    def verified_account(self):
        profile = getattr(self, '_confirmed_identity', None)
        if (not profile or not (self.status or {}).get('login_successful')
                or self.close_requested or not self._session or self._session.closed
                or profile['customerId'] != self.customer_id):
            return None
        return dict(profile)

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
        self._confirmed_identity = None
        self._identity_candidate = None
        self._identity_candidate_domain = None
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
        # GET redirects can consume a callback before the final page arrives.
        # Capture only trusted callback URLs, including intermediate locations.
        for previous in getattr(resp, 'history', ()):
            self._capture_oauth_callback(previous.url)
            location = previous.headers.get('Location')
            if location:
                self._capture_oauth_callback(urljoin(str(previous.url), location))
        self._capture_oauth_callback(resp.url)
        url = await super()._process_resp(resp)
        self._resp_url = str(resp.url)
        self._resp_status = resp.status
        if getattr(self, '_identity_probe_active', False) and resp.status == 200:
            parsed = urlsplit(str(resp.url))
            if (parsed.scheme == 'https' and parsed.hostname in
                    {'www.' + self.url.lower(), 'www.amazon.com',
                     'alexa.' + self.url.lower(), 'alexa.amazon.com'}
                    and parsed.path == '/api/users/me' and parsed.port in (None, 443)
                    and not parsed.username and not parsed.password):
                try:
                    customer = await resp.json()
                except Exception:
                    customer = None
                if (isinstance(customer, dict) and isinstance(customer.get('email'), str)
                        and '@' in customer['email'] and isinstance(customer.get('id'), str)
                        and customer['id'].strip()):
                    self._identity_candidate_domain = parsed.hostname.split('.', 1)[1]
                    self._identity_candidate = {'authenticated': True,
                        'customerEmail': customer['email'], 'customerId': customer['id']}
        return url

    async def _process_page(self, html, site):
        self._page_url = getattr(self, "_resp_url", None) or str(site)
        self._page_status = getattr(self, '_resp_status', None)
        self._page_html = html
        return await super()._process_page(html, site)
