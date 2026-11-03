"""The sidecar's automated sign-in steps, driven without a browser.

Nothing here can prove that Google's form behaves as documented; what it does
prove is the contract the application server relies on: every step is token-gated,
a profile key cannot escape its directory, one Chromium serves one ladder at a
time, a terminal page state releases the browser, and the credential that arrives
in a request body never comes back out in a response.
"""
import importlib.util
import json
import os
import sys
import time
import unittest

_SIDECAR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'browser-auth'))
if os.path.isdir(_SIDECAR):
    sys.path.insert(0, _SIDECAR)

    import service as svc  # noqa: E402
    import google_signin as gs  # noqa: E402
else:
    # The sidecar ships in its own image; the classes below stand down without it.
    svc = gs = None
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sidecar_support import requires_sidecar  # noqa: E402

TOKEN = 'a-control-token'
EMAIL = 'server.account@gmail.com'
PASSWORD = 'a-password-nobody-else-has'
CODE = '123456'


class _LiveThread:
    def is_alive(self):
        return True


class FakePage:
    url = 'https://accounts.google.com/v3/signin/identifier'

    def is_closed(self):
        return False


class FakeContext:
    def __init__(self, cookies=()):
        self._cookies = list(cookies)

    def cookies(self, _url):
        return self._cookies


@requires_sidecar('service.py')
class StepTests(unittest.TestCase):
    def setUp(self):
        self.saved = []
        svc.CONTROL_TOKEN = TOKEN
        svc._worker = None
        svc._candidate = None
        svc._automation = None
        svc._profile_key = ''
        svc._state.update(state='idle', interactive=False, message=None, started_at=None)
        self.client = svc.app.test_client()
        self.detached = []
        self.stub(svc, '_detach_automation', lambda: (self.detached.append(True),
                                                      setattr(svc, '_automation', None)))
        self.stub(svc, '_attach_automation', lambda profile_dir: FakePage())
        self.stub(svc, '_manual_login', lambda **kwargs: None)
        self.stub(svc, '_stop_manual_browser', lambda: None)

    def tearDown(self):
        for target, name, value in reversed(self.saved):
            setattr(target, name, value)

    def stub(self, target, name, value):
        self.saved.append((target, name, getattr(target, name)))
        setattr(target, name, value)

    def attach_to(self, page):
        self.stub(svc, '_attach_automation', lambda profile_dir: page)
        svc._automation = {'page': page, 'context': FakeContext(), 'profile_dir': '/profile'}
        return page

    def post(self, path, body=None, token=TOKEN):
        headers = {'X-Control-Token': token, 'X-Auth-Attempt':'test-attempt'} if token else {}
        svc._auth_attempt='test-attempt';svc._auth_attempt_deadline=time.monotonic()+300
        return self.client.post(path, data=json.dumps(body or {}),
                                content_type='application/json', headers=headers)

    def record_steps(self, outcome_by_index=None, outcomes=None):
        """Replace every typing helper with one that logs its arguments."""
        seen = []

        def make(kind):
            def handler(page, *args, **kwargs):
                seen.append((kind, page, args))
                outcome = (outcomes or {}).get(kind, gs.NEED_PASSWORD)
                return {'url': 'https://accounts.google.com/x', 'text': '',
                        'fields': {}}, outcome
            return handler

        for kind in ('open_signin', 'submit_identifier', 'submit_password', 'submit_code',
                     'choose_two_step', 'accept_consent'):
            self.stub(gs, kind, make(kind))
        return seen


class GateTests(StepTests):
    def test_every_google_step_requires_the_control_token(self):
        for path in ('/google/start', '/google/identifier', '/google/password',
                     '/google/code', '/google/capture', '/google/stop', '/google/state'):
            for token in (None, 'wrong-token'):
                with self.subTest(path=path, token=token):
                    response = self.post(path, token=token)
                    self.assertEqual(response.status_code, 401)
        # An unauthenticated caller must not have been able to open a browser.
        self.assertIsNone(svc._automation)

    def test_a_profile_key_cannot_climb_out_of_the_profile_directory(self):
        self.assertEqual(svc._profile_dir_for(''), svc.PROFILE_DIR)
        self.assertEqual(svc._profile_dir_for('5f0b-user'),
                         os.path.join(svc.PROFILE_DIR, '5f0b-user'))
        for key in ('../escape', 'sub/dir', '.hidden', '-leading', '#' * 70):
            with self.subTest(key=key):
                self.assertRaises(ValueError, svc._profile_dir_for, key)

    def test_an_invalid_profile_key_is_refused_before_any_browser_starts(self):
        response = self.post('/google/start', {'profile': '../etc'})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json(), {'error': 'invalid_profile_key'})
        self.assertIsNone(svc._automation)

    def test_a_step_wont_share_the_browser_with_a_human_or_a_worker(self):
        for busy in ('worker', 'interactive'):
            with self.subTest(busy=busy):
                svc._worker = _LiveThread() if busy == 'worker' else None
                svc._state['interactive'] = busy == 'interactive'
                response = self.post('/google/start', {'profile': ''})
                self.assertEqual(response.status_code, 409)
                self.assertEqual(response.get_json(), {'outcome': 'browser_in_use'})
                self.assertIsNone(svc._automation)
        svc._worker = None
        svc._state['interactive'] = False

    def test_a_malformed_code_is_rejected_rather_than_typed_into_a_challenge(self):
        # Four to sixteen characters covers a six-digit authenticator code and an
        # eight-character backup code alike; anything else is not a code at all.
        for code in ('', 'x' * 40, '1234 567', 'DROP TABLE', 'a' * 3 + '\n'):
            with self.subTest(code=code):
                response = self.post('/google/code', {'profile': '', 'code': code})
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.get_json(), {'error': 'invalid_code'})


class AttemptLifecycleTests(StepTests):
    def test_stop_retires_the_capability_and_a_new_start_can_claim(self):
        self.record_steps();self.attach_to(FakePage())
        self.post('/google/start', {'profile': ''})
        response=self.post('/google/stop', {'profile': ''})
        self.assertEqual(response.status_code,200)
        self.assertEqual(svc._auth_attempt,'')
        response=self.client.post('/google/start',json={'profile':''},headers={'X-Control-Token':TOKEN,'X-Auth-Attempt':'replacement-attempt'})
        self.assertNotEqual(response.status_code,403)
        self.assertEqual(svc._auth_attempt,'replacement-attempt')


class StepWiringTests(StepTests):
    def test_each_step_types_only_its_own_credential_into_only_its_own_helper(self):
        seen = self.record_steps()
        self.attach_to(FakePage())
        self.post('/google/start', {'profile': ''})
        self.post('/google/identifier', {'profile': '', 'email': EMAIL})
        self.post('/google/password', {'profile': '', 'password': PASSWORD})
        self.post('/google/code', {'profile': '', 'code': CODE})
        self.post('/google/choice', {'profile': ''})
        self.post('/google/consent', {'profile': ''})
        self.assertEqual([kind for kind, _p, _a in seen],
                         ['open_signin', 'submit_identifier', 'submit_password',
                          'submit_code', 'choose_two_step', 'accept_consent'])
        # The address goes to the identifier step and nowhere else, the password to
        # one step, and the code to one step.
        self.assertEqual(seen[1][2], (EMAIL,))
        self.assertEqual(seen[2][2], (PASSWORD,))
        self.assertEqual(seen[3][2], (CODE,))
        self.assertEqual(seen[4][2], ())
        for index, (kind, _page, args) in enumerate(seen):
            if index != 1:
                self.assertNotIn(EMAIL, args, f'{kind} was handed the address')
            if index != 2:
                self.assertNotIn(PASSWORD, args, f'{kind} was handed the password')

    def test_no_step_answers_with_a_credential_or_a_page_body(self):
        self.record_steps()
        self.attach_to(FakePage())
        for path, body in (('/google/start', {}), ('/google/identifier', {'email': EMAIL}),
                           ('/google/password', {'password': PASSWORD}),
                           ('/google/code', {'code': CODE})):
            with self.subTest(path=path):
                payload = self.post(path, body).get_json()
                self.assertEqual(sorted(payload), ['outcome', 'url'])
                self.assertNotIn(PASSWORD, json.dumps(payload))
                self.assertNotIn(EMAIL, json.dumps(payload))
                self.assertNotIn(CODE, json.dumps(payload))

    def test_the_step_remembers_the_profile_it_is_working_on(self):
        self.record_steps()
        self.attach_to(FakePage())
        self.post('/google/start', {'profile': 'user-42'})
        self.assertEqual(svc._profile_key, os.path.join(svc.PROFILE_DIR, 'user-42'))

    def test_a_url_never_leaves_with_its_query_string_attached(self):
        page = FakePage()
        page.url = 'https://accounts.google.com/x?hl=en&continue=https%3A%2F%2Fmusic'
        self.stub(svc, '_attach_automation', lambda profile_dir: page)
        self.stub(gs, 'open_signin',
                  lambda p, *a, **k: ({'url': p.url, 'text': '', 'fields': {}},
                                      gs.NEED_PASSWORD))
        payload = self.post('/google/start', {}).get_json()
        self.assertEqual(payload['url'], 'accounts.google.com/x')


@requires_sidecar('service.py')
class GoogleSessionTests(unittest.TestCase):
    class Jar:
        def __init__(self, cookies):
            self._cookies = list(cookies)

        def cookies(self):
            return self._cookies

    def test_a_google_session_is_recognised_by_its_auth_cookies(self):
        for name in ('SID', '__Secure-1PSID', 'SAPISID'):
            with self.subTest(name=name):
                self.assertTrue(svc._google_session_present(self.Jar([
                    {'domain': '.google.com', 'name': name, 'value': 'v'}])))

    def test_visitor_cookies_do_not_count_as_a_google_session(self):
        self.assertFalse(svc._google_session_present(self.Jar([
            {'domain': '.google.com', 'name': 'NID', 'value': 'v'},
            {'domain': '.youtube.com', 'name': 'YSC', 'value': 'v'},
            {'domain': '.youtube.com', 'name': 'VISITOR_INFO1_LIVE', 'value': 'v'}])))

    def test_a_jar_that_cannot_be_read_is_not_a_session(self):
        class Broken:
            def cookies(self):
                raise RuntimeError('closed')

        self.assertFalse(svc._google_session_present(Broken()))


class ReleaseTests(StepTests):
    def test_a_terminal_page_state_releases_the_browser_and_a_retryable_one_does_not(self):
        terminal = (gs.UNSUPPORTED, gs.BLOCKED, gs.LOCKED, gs.WRONG_PASSWORD,
                    gs.UNKNOWN_ACCOUNT, gs.TIMEOUT)
        keepable = (gs.SIGNED_IN, gs.NEED_IDENTIFIER, gs.NEED_PASSWORD, gs.NEED_TOTP,
                    gs.NEED_BACKUP_CODE, gs.NEED_CHOICE, gs.CONSENT, gs.NEED_PROMPT,
                    gs.NEED_PHONE, gs.NEED_PASSKEY, gs.CAPTCHA)
        for outcome in terminal + keepable:
            with self.subTest(outcome=outcome):
                self.detached.clear()
                svc._automation = {'page': FakePage(), 'context': FakeContext(),
                                   'profile_dir': '/profile'}
                self.record_steps(outcomes={'open_signin': outcome})
                self.stub(svc, '_attach_automation', lambda profile_dir: FakePage())
                self.post('/google/start', {})
                self.assertEqual(bool(self.detached), outcome in terminal,
                                 'a terminal state must flush the profile, a live '
                                 'challenge must stay open for the next answer')
                if outcome == gs.SIGNED_IN:
                    self.assertFalse(self.detached,
                                     'signed_in is kept open for the capture step')

    def test_a_step_that_raises_is_a_dead_browser_not_a_login_answer(self):
        def explode(*_args, **_kwargs):
            raise RuntimeError('page crashed')

        self.stub(gs, 'open_signin', explode)
        self.stub(svc, '_attach_automation', lambda profile_dir: FakePage())
        svc._automation = {'page': FakePage(), 'context': FakeContext(),
                           'profile_dir': '/profile'}
        response = self.post('/google/start', {})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.get_json(), {'outcome': 'crashed'})
        self.assertTrue(self.detached)

    def test_stop_always_releases_the_browser_and_returns_the_service_to_idle(self):
        self.stub(svc, '_attach_automation', lambda profile_dir: FakePage())
        svc._automation = {'page': FakePage(), 'context': FakeContext(),
                           'profile_dir': '/profile'}
        svc._state.update(state='signing_in')
        payload = self.post('/google/stop', {}).get_json()
        self.assertEqual(payload, {'outcome': 'stopped'})
        self.assertIsNone(svc._automation)
        self.assertEqual(svc._state['state'], 'idle')

    def test_a_capture_without_an_attached_browser_is_answered_as_not_signed_in(self):
        svc._automation = None
        response = self.post('/google/capture', {})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json(), {'outcome': 'not_signed_in'})

    def test_retired_attempt_cannot_read_state(self):
        self.post('/google/stop', {})
        response = self.client.get('/google/state', headers={
            'X-Control-Token': TOKEN, 'X-Auth-Attempt': 'test-attempt'})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json(), {'error': 'attempt_expired'})


class CaptureExportTests(StepTests):
    def test_a_capture_hands_back_the_headers_once_and_the_jar_with_them(self):
        page = FakePage()
        self.stub(svc, '_attach_automation', lambda profile_dir: page)
        svc._automation = {'page': page,
                           'context': FakeContext([
                               {'domain': '.youtube.com', 'name': 'SSID', 'value': 'v',
                                'path': '/', 'expires': -1, 'httponly': False,
                                'secure': True}]),
                           'profile_dir': '/profile'}
        self.stub(svc, '_capture_from_running_browser', lambda interactive, connection=None: 'captured')
        svc._candidate = {'url': 'https://music.youtube.com/youtubei/v1/account/account_menu',
                          'headers': {'cookie': 'SAPISID=x'}}
        payload = self.post('/google/capture', {}).get_json()
        self.assertEqual(payload['outcome'], 'captured')
        self.assertEqual(payload['headers'], {'cookie': 'SAPISID=x'})
        self.assertIn('SSID', payload['cookies'])
        self.assertIsNone(svc._candidate, 'the candidate must not survive its own export')
        self.assertFalse(svc._automation, 'the browser is released with the session')

    def test_a_captured_session_cannot_be_taken_a_second_time(self):
        page = FakePage()
        self.stub(svc, '_attach_automation', lambda profile_dir: page)
        svc._automation = {'page': page, 'context': FakeContext([
            {'domain': '.youtube.com', 'name': 'SSID', 'value': 'v'}]),
            'profile_dir': '/profile'}
        self.stub(svc, '_capture_from_running_browser', lambda interactive, connection=None: 'captured')
        svc._candidate = {'url': 'https://music.youtube.com/youtubei/v1/x',
                          'headers': {'cookie': 'SAPISID=x'}}
        self.post('/google/capture', {})
        # The capture path is finished, so the second call finds no browser at all;
        # and even with one, the candidate is gone from the process.
        self.assertIsNone(svc._candidate)
        self.assertEqual(self.client.post('/candidate/take', data='{}',
                                          content_type='application/json',
                                          headers={'X-Control-Token': TOKEN, 'X-Auth-Attempt':'test-attempt'}).status_code, 404)

    def test_a_signed_in_page_with_nothing_worth_exporting_ships_an_empty_jar(self):
        page = FakePage()
        self.stub(svc, '_attach_automation', lambda profile_dir: page)
        svc._automation = {'page': page, 'context': FakeContext([]),
                           'profile_dir': '/profile'}
        self.stub(svc, '_capture_from_running_browser', lambda interactive, connection=None: 'captured')
        svc._candidate = {'url': 'https://music.youtube.com/youtubei/v1/x',
                          'headers': {'cookie': 'a=1'}}
        payload = self.post('/google/capture', {}).get_json()
        self.assertEqual(payload['outcome'], 'captured')
        self.assertEqual([line for line in payload['cookies'].splitlines()
                          if line and not line.startswith('#')], [])

    def test_a_capture_that_failed_exports_nothing_and_consumes_nothing(self):
        page = FakePage()
        self.stub(svc, '_attach_automation', lambda profile_dir: page)
        svc._automation = {'page': page, 'context': FakeContext([
            {'domain': '.youtube.com', 'name': 'SSID', 'value': 'v'}]),
            'profile_dir': '/profile'}
        self.stub(svc, '_capture_from_running_browser', lambda interactive, connection=None: 'signed_out')
        svc._candidate = None
        payload = self.post('/google/capture', {}).get_json()
        self.assertEqual(payload['outcome'], 'signed_out')
        self.assertNotIn('headers', payload)
        self.assertFalse(svc._automation)

    def test_a_browser_that_died_during_the_export_still_delivers_the_headers(self):
        class DyingContext:
            def cookies(self, _url):
                raise RuntimeError('connection closed')

        page = FakePage()
        self.stub(svc, '_attach_automation', lambda profile_dir: page)
        svc._automation = {'page': page, 'context': DyingContext(), 'profile_dir': '/profile'}
        self.stub(svc, '_capture_from_running_browser', lambda interactive, connection=None: 'captured')
        svc._candidate = {'url': 'https://music.youtube.com/youtubei/v1/x',
                          'headers': {'cookie': 'SAPISID=x'}}
        payload = self.post('/google/capture', {}).get_json()
        self.assertEqual(payload['outcome'], 'captured')
        self.assertEqual(payload['headers'], {'cookie': 'SAPISID=x'})

    def test_a_candidate_taken_underneath_the_capture_is_reported_as_no_session(self):
        page = FakePage()
        self.stub(svc, '_attach_automation', lambda profile_dir: page)
        svc._automation = {'page': page, 'context': FakeContext([]), 'profile_dir': '/profile'}

        def race_capture(_interactive, connection=None):
            # Something else consumed the candidate between capture and export.
            svc._candidate = None
            return 'captured'

        self.stub(svc, '_capture_from_running_browser', race_capture)
        self.assertEqual(self.post('/google/capture', {}).get_json()['outcome'], 'timeout')


@requires_sidecar('service.py')
class CookieJarTests(unittest.TestCase):
    def test_only_youtubes_own_domains_are_written_to_the_jar(self):
        jar = svc._netscape_jar([
            {'domain': '.youtube.com', 'name': 'SSID', 'value': 'a', 'path': '/',
             'expires': 2000000000, 'httponly': True, 'secure': True},
            {'domain': '.google.com', 'name': 'SID', 'value': 'secret', 'path': '/',
             'expires': 2000000000},
            {'domain': 'music.youtube.com', 'name': 'VISITOR_INFO', 'value': 'b'},
            {'domain': 'accounts.google.com', 'name': 'HSID', 'value': 'c'},
            {'domain': '', 'name': 'x', 'value': 'y'},
            {'domain': '.youtube.com', 'name': 'empty', 'value': ''},
        ])
        self.assertIn('# Netscape HTTP Cookie File', jar)
        # A "#HttpOnly_" row is a data row: that prefix is part of the file format.
        rows = [line for line in jar.splitlines()
                if line and (line.startswith('#Http') or not line.startswith('#'))]
        self.assertEqual(len(rows), 2, jar)
        self.assertTrue(all('google.com' not in row for row in rows), rows)
        self.assertIn('#HttpOnly_.youtube.com', rows[0])
        self.assertEqual(rows[0].split('\t')[4], '2000000000')
        self.assertNotIn('secret', jar)

    def test_a_session_cookie_gets_a_horizon_rather_than_an_expired_zero(self):
        # download_cookies refuses a jar whose only cookie is already dead, so a
        # browser session cookie must not be written with an expiry in the past.
        jar = svc._netscape_jar([{'domain': '.youtube.com', 'name': 'NID', 'value': 'v',
                                  'expires': -1}])
        fields = [line for line in jar.splitlines() if 'NID' in line][0].split('\t')
        self.assertGreater(float(fields[4]), time.time())

    def test_the_same_cookie_from_two_hosts_is_not_duplicated(self):
        jar = svc._netscape_jar([
            {'domain': 'youtube.com', 'name': 'PREF', 'value': '1', 'path': '/'},
            {'domain': '.youtube.com', 'name': 'PREF', 'value': '1', 'path': '/'},
        ])
        self.assertEqual(len([line for line in jar.splitlines() if 'PREF' in line]), 1)


if __name__ == '__main__':
    unittest.main()
