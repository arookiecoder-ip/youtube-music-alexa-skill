"""The server's own Google credential, walked through the sign-in ladder.

This is the code that types a password and an authenticator code into a browser
without a human watching, so the properties that matter are: it gives the right
answer to the right question, it stops the moment the question is one it cannot
answer, it never repeats a failure Google has already refused, and nothing it was
told ever comes back out in a status dict, an alert or a log line.
"""
import importlib.util
import os
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

_SIDECAR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'browser-auth'))


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(_SIDECAR, filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# google_signin.py is the sidecar's own module: it answers the questions Google
# asks in the browser, and it only exists where browser-auth ships.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sidecar_support import BROWSER_AUTH, requires_sidecar  # noqa: E402

gs = _load('google_signin_for_auto_login', 'google_signin.py') if (BROWSER_AUTH / 'google_signin.py').is_file() else None
import youtube_auto_login as auto  # noqa: E402

EMAIL = 'server.account@gmail.com'
PASSWORD = 'a-password-nobody-else-has'
# The RFC 6238 test secret, so the vector below is Google's and ours at once.
SECRET = 'GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ'
BACKUP = ('abcdef12', 'ghijkl34')

CAPTURED = {'outcome': 'captured', 'headers': {'cookie': 'SAPISID=x'},
            'cookies': '# Netscape HTTP Cookie File\n'}


class FakeController:
    """The sidecar, scripted one answer at a time.

    A path with no scripted answer returns ``unscripted`` rather than a plausible
    success: a ladder that wanders into a step the test did not write down must
    fail loudly instead of quietly signing in.
    """

    def __init__(self, script=(), on_call=None):
        self.script = list(script)
        self.calls = []
        self.on_call = on_call

    def call(self, path, method='POST', payload=None):
        self.calls.append((path, method, dict(payload or {})))
        if self.on_call is not None:
            self.on_call(path, payload or {})
        for index, (want, response) in enumerate(self.script):
            if want == path:
                del self.script[index]
                if isinstance(response, Exception):
                    raise response
                return response
        return {'outcome': 'unscripted'}

    def paths(self):
        return [path for path, _method, _payload in self.calls]

    def codes_typed(self):
        return [body['code'] for path, _m, body in self.calls if path == '/google/code']

    def payload(self, path):
        for want, _method, body in self.calls:
            if want == path:
                return body
        raise AssertionError(f'{path} was never called')

    def stray_secrets(self, *values):
        """Credentials that appear in a payload other than the one step that needs them."""
        expected = {'/google/start': ('email',), '/google/identifier': ('email',),
                    '/google/password': ('password',), '/google/code': ('code',)}
        stray = []
        for path, _method, body in self.calls:
            for key, value in body.items():
                if key in expected.get(path, ()) and value in values:
                    continue
                if value in values:
                    stray.append((path, key))
        return stray


def login(controller, *, email=EMAIL, password=PASSWORD, credential=SECRET,
          alert=None, on_session=None, clock=None, sleep=None, **kwargs):
    account = auto.ServerAccount(email, password, credential, clock=clock or time.time)
    return auto.GoogleAutoLogin(controller, account=account, on_session=on_session,
                                alert=alert, clock=clock or time.time,
                                sleep=sleep or (lambda _seconds: None), **kwargs)


def silent(*_args):
    """An alert sink for tests that are not about alerting."""


class CredentialShapeTests(unittest.TestCase):
    """An operator pastes either a secret or a list of codes, and the difference
    decides whether this feature is a keep-alive or a countdown."""

    def test_a_contiguous_base32_value_is_a_secret(self):
        self.assertEqual(auto.parse_credential(SECRET), (auto.SECRET_MODE, SECRET))

    def test_the_same_secret_pasted_in_groups_of_four_is_still_a_secret(self):
        spaced = ' '.join(SECRET[i:i + 4] for i in range(0, len(SECRET), 4))
        self.assertEqual(auto.parse_credential(spaced), (auto.SECRET_MODE, SECRET))

    def test_a_lowercase_secret_is_normalised_not_rejected(self):
        self.assertEqual(auto.parse_credential(SECRET.lower()), (auto.SECRET_MODE, SECRET))

    def test_a_list_of_eight_character_codes_is_a_code_list(self):
        for joined in (' '.join(BACKUP), ', '.join(BACKUP), ';'.join(BACKUP)):
            with self.subTest(joined=joined):
                mode, codes = auto.parse_credential(joined)
                self.assertEqual((mode, list(codes)), (auto.CODES_MODE, list(BACKUP)))

    def test_two_codes_that_happen_to_be_valid_base32_are_still_codes(self):
        # 'ABCDEFGHIJKLMNOP' squashes to legal base32 of a secret length. Reading it
        # as a secret would mean generating codes from someone's backup list forever.
        self.assertEqual(auto.parse_credential('abcdefgh ijklmnop'),
                         (auto.CODES_MODE, ['abcdefgh', 'ijklmnop']))

    def test_a_single_short_value_is_one_backup_code(self):
        self.assertEqual(auto.parse_credential(BACKUP[0]), (auto.CODES_MODE, [BACKUP[0]]))

    def test_rubbish_is_refused_rather_than_guessed_at(self):
        for value in ('', '   ', 'not a credential at all!!', 'abc'):
            with self.subTest(value=value):
                self.assertEqual(auto.parse_credential(value), (auto.NONE_MODE, None))

    def test_an_account_is_configured_only_when_all_three_parts_are_present(self):
        self.assertTrue(auto.ServerAccount(EMAIL, PASSWORD, SECRET).configured)
        for email, password, credential in (
                ('', PASSWORD, SECRET), (EMAIL, '', SECRET), (EMAIL, PASSWORD, ''),
                (EMAIL, PASSWORD, 'abc')):
            with self.subTest(email=email, password=password):
                self.assertFalse(auto.ServerAccount(email, password, credential).configured)


class TotpTests(unittest.TestCase):
    def test_the_known_rfc_vector(self):
        # RFC 6238 appendix B: secret "12345678901234567890", time 59 -> counter 1
        # -> 94287082, which is 287082 at the six digits Google asks for.
        self.assertEqual(auto.totp_at(SECRET, 59 // 30), '287082')

    def test_a_code_is_always_six_digits(self):
        for counter in range(0, 500, 37):
            code = auto.totp_at(SECRET, counter)
            self.assertTrue(len(code) == 6 and code.isdigit(), code)

    def test_a_broken_secret_gives_no_code_rather_than_raising(self):
        self.assertEqual(auto.AnswerSupply(auto.SECRET_MODE, 'A1').next(), '')

    def test_a_negative_counter_is_a_refused_value_not_a_struct_crash(self):
        with self.assertRaises(ValueError):
            auto.totp_at(SECRET, -1)

    def test_a_clock_before_the_epoch_still_answers_the_windows_it_can(self):
        # A container restored from an old snapshot can genuinely have a clock in
        # 1970. That must read as "no answer for this window", never as a dead
        # browser service, and never lose the windows that are computable.
        supply = auto.AnswerSupply(auto.SECRET_MODE, SECRET, clock=lambda: 0)
        given = [supply.next() for _ in range(len(supply.WINDOWS))]
        self.assertEqual(given, [auto.totp_at(SECRET, 0), '', auto.totp_at(SECRET, 1),
                                 '', auto.totp_at(SECRET, 2)])


class AnswerSupplyTests(unittest.TestCase):
    def test_a_secret_walks_the_clock_windows_then_answers_nothing(self):
        now = 1_700_000_000.0
        counter = int(now) // 30
        supply = auto.AnswerSupply(auto.SECRET_MODE, SECRET, clock=lambda: now)
        given = [supply.next() for _ in range(len(supply.WINDOWS) + 1)]
        self.assertEqual(given[:-1],
                         [auto.totp_at(SECRET, counter + offset) for offset in supply.WINDOWS])
        self.assertEqual(given[-1], '')
        self.assertEqual(supply.remaining, 0)

    def test_each_backup_code_is_offered_exactly_once_and_in_order(self):
        supply = auto.AnswerSupply(auto.CODES_MODE, codes=list(BACKUP))
        first = supply.next()
        supply.spent()
        self.assertEqual(first, BACKUP[0])
        self.assertEqual(supply.next(), BACKUP[1])
        # A code Google refused is not typed again; the ladder moves to the next one,
        # because repeating a refused answer only spends the account's attempt budget.
        supply.spent()
        self.assertEqual(supply.next(), '')

    def test_an_exhausted_list_answers_nothing(self):
        supply = auto.AnswerSupply(auto.CODES_MODE, codes=[BACKUP[0]])
        supply.next()
        supply.spent()
        self.assertEqual(supply.next(), '')

    def test_the_description_names_the_shape_and_never_the_value(self):
        self.assertEqual(auto.AnswerSupply(auto.SECRET_MODE, SECRET).describe(),
                         'authenticator secret')
        described = auto.AnswerSupply(auto.CODES_MODE, codes=list(BACKUP)).describe()
        self.assertEqual(described, '2 backup code(s) left')
        self.assertNotIn(BACKUP[0], described)
        self.assertEqual(auto.AnswerSupply(auto.NONE_MODE).describe(), 'no 2-step credential')


@requires_sidecar('google_signin.py')
class LadderTests(unittest.TestCase):
    def test_the_happy_path_from_an_empty_browser_to_a_saved_session(self):
        controller = FakeController([
            ('/google/start', {'outcome': gs.NEED_IDENTIFIER}),
            ('/google/identifier', {'outcome': gs.NEED_PASSWORD}),
            ('/google/password', {'outcome': gs.NEED_TOTP, 'code_kind': 'totp'}),
            ('/google/code', {'outcome': gs.SIGNED_IN}),
            ('/google/capture', CAPTURED),
        ])
        sessions = []
        result = login(controller, on_session=lambda h, c: sessions.append((h, c))
                       ).sign_in('owner', reason='scheduled')
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['outcome'], 'signed_in')
        self.assertEqual(controller.paths()[:5],
                         ['/google/start', '/google/identifier', '/google/password',
                          '/google/code', '/google/capture'])
        self.assertEqual(sessions, [({'cookie': 'SAPISID=x'}, '# Netscape HTTP Cookie File\n')])

    def test_a_success_is_not_reported_in_the_failure_wording(self):
        # The admin card renders describe_outcome(last_outcome) for every run. A
        # success with no sentence fell through to "stopped at an unexpected
        # state", so a working keep-alive read as an error on the page.
        controller = FakeController([('/google/start', {'outcome': gs.SIGNED_IN}),
                                     ('/google/capture', CAPTURED)])
        driver = login(controller)
        driver.sign_in('owner')
        text = auto.describe_outcome(driver.status()['last_outcome'])
        self.assertNotIn('unexpected', text)
        self.assertIn('download cookies', text)

    def test_a_profile_that_is_still_signed_in_goes_straight_to_the_capture(self):
        # Google bounced the browser onto music.youtube.com by itself: typing a
        # password over a working session would only invalidate it.
        controller = FakeController([('/google/start', {'outcome': gs.SIGNED_IN}),
                                     ('/google/capture', CAPTURED)])
        self.assertTrue(login(controller).sign_in('owner')['ok'])
        self.assertNotIn('/google/password', controller.paths())

    def test_a_missed_time_window_is_answered_with_the_next_code(self):
        controller = FakeController([
            ('/google/start', {'outcome': gs.NEED_TOTP, 'code_kind': 'totp'}),
            ('/google/code', {'outcome': gs.NEED_TOTP, 'code_kind': 'totp'}),
            ('/google/code', {'outcome': gs.NEED_TOTP, 'code_kind': 'totp'}),
            ('/google/code', {'outcome': gs.SIGNED_IN}),
            ('/google/capture', CAPTURED),
        ])
        login(controller, clock=lambda: 30.0).sign_in('owner')
        self.assertEqual(controller.codes_typed(),
                         [auto.totp_at(SECRET, 1 + offset)
                          for offset in auto.AnswerSupply.WINDOWS[:3]])

    def test_a_refused_backup_code_is_replaced_not_retyped(self):
        controller = FakeController([
            ('/google/start', {'outcome': gs.NEED_BACKUP_CODE, 'code_kind': 'backup'}),
            ('/google/code', {'outcome': gs.BAD_CODE, 'detail': 'no'}),
            ('/google/code', {'outcome': gs.SIGNED_IN}),
            ('/google/capture', CAPTURED),
        ])
        result = login(controller, credential=' '.join(BACKUP)).sign_in('owner')
        self.assertTrue(result['ok'], result)
        # Google said no once; typing the same code again would only teach it that
        # a bot is here, so the next code in the list is the one that goes in.
        self.assertEqual(controller.codes_typed(), [BACKUP[0], BACKUP[1]])

    def test_a_code_google_accepted_is_not_offered_by_the_next_run(self):
        account = auto.ServerAccount(EMAIL, PASSWORD, ' '.join(BACKUP))
        first = FakeController([
            ('/google/start', {'outcome': gs.NEED_BACKUP_CODE, 'code_kind': 'backup'}),
            ('/google/code', {'outcome': gs.NEED_PASSWORD}),
            ('/google/password', {'outcome': gs.CAPTCHA}),
        ])
        driver = auto.GoogleAutoLogin(first, account=account, alert=silent,
                                      sleep=lambda _s: None)
        self.assertFalse(driver.sign_in('owner')['ok'])
        self.assertEqual(first.codes_typed(), [BACKUP[0]])
        # Google spent that code for everyone, so the countdown the panel shows has
        # to outlive the run that spent it.
        self.assertEqual(account.describe()['codes_remaining'], 1)
        second = FakeController([
            ('/google/start', {'outcome': gs.NEED_BACKUP_CODE, 'code_kind': 'backup'}),
            ('/google/code', {'outcome': gs.SIGNED_IN}),
            ('/google/capture', CAPTURED),
        ])
        result = auto.GoogleAutoLogin(second, account=account, alert=silent,
                                      sleep=lambda _s: None).sign_in('owner')
        self.assertTrue(result['ok'], result)
        self.assertEqual(second.codes_typed(), [BACKUP[1]])

    def test_an_exhausted_credential_stops_before_google_sees_a_fifth_guess(self):
        account = auto.ServerAccount(EMAIL, PASSWORD, BACKUP[0])
        controller = FakeController([
            ('/google/start', {'outcome': gs.NEED_BACKUP_CODE, 'code_kind': 'backup'}),
            ('/google/code', {'outcome': gs.NEED_BACKUP_CODE, 'code_kind': 'backup'}),
        ])
        result = auto.GoogleAutoLogin(controller, account=account, alert=silent,
                                      sleep=lambda _s: None).sign_in('owner')
        self.assertEqual(result['outcome'], auto._NO_ANSWER)
        self.assertFalse(result['ok'])
        self.assertEqual(len(controller.codes_typed()), 1)

    def test_a_two_step_choice_and_a_consent_wall_are_answered_not_abandoned(self):
        controller = FakeController([
            ('/google/start', {'outcome': gs.NEED_CHOICE}),
            ('/google/choice', {'outcome': gs.CONSENT}),
            ('/google/consent', {'outcome': gs.NEED_PASSWORD}),
            ('/google/password', {'outcome': gs.SIGNED_IN}),
            ('/google/capture', CAPTURED),
        ])
        self.assertTrue(login(controller).sign_in('owner')['ok'])

    def test_an_address_google_refuses_stops_instead_of_typing_it_again(self):
        # Every failed attempt is a data point for their abuse model, and twelve
        # identical ones look exactly like a credential-stuffing run.
        controller = FakeController([
            ('/google/start', {'outcome': gs.NEED_IDENTIFIER}),
            ('/google/identifier', {'outcome': gs.NEED_IDENTIFIER}),
        ])
        result = login(controller, alert=silent).sign_in('owner')
        self.assertEqual(result['outcome'], gs.NEED_IDENTIFIER)
        self.assertEqual(controller.paths().count('/google/identifier'), 1)
        self.assertTrue(result['detail'])

    def test_every_question_this_server_cannot_answer_reaches_the_operator(self):
        unanswerable = (gs.NEED_PROMPT, gs.NEED_PHONE, gs.NEED_PASSKEY, gs.CAPTCHA,
                        gs.BLOCKED, gs.LOCKED, gs.UNKNOWN_ACCOUNT, gs.WRONG_PASSWORD,
                        gs.UNSUPPORTED, gs.TIMEOUT)
        for outcome in unanswerable:
            with self.subTest(outcome=outcome):
                alerts = []
                controller = FakeController([('/google/start', {'outcome': outcome})])
                driver = login(controller, alert=lambda *args: alerts.append(args))
                result = driver.sign_in('owner')
                self.assertFalse(result['ok'])
                self.assertEqual(result['outcome'], outcome)
                self.assertTrue(result['detail'], f'{outcome} alerts with no reason')
                self.assertEqual(len(alerts), 1)
                self.assertTrue(driver.status()['needs_human'],
                                f'{outcome} is a human problem the panel never shows')

    def test_a_page_that_keeps_changing_the_question_gives_up_at_the_step_cap(self):
        script = [('/google/start', {'outcome': gs.NEED_PASSWORD})]
        script += [('/google/password', {'outcome': gs.NEED_PASSWORD})] * (
            auto.GoogleAutoLogin.max_steps + 2)
        controller = FakeController(script)
        result = login(controller, alert=silent).sign_in('owner')
        self.assertEqual(result['outcome'], gs.TIMEOUT)
        # One call per step plus the sign-in request itself; the teardown /google/stop
        # is not part of the budget because it carries no credential.
        self.assertEqual(len([path for path in controller.paths()
                              if path != '/google/stop']), auto.GoogleAutoLogin.max_steps + 1)

    def test_a_sidecar_that_raises_is_a_dead_service_not_a_crash(self):
        controller = FakeController([('/google/start', RuntimeError('connection refused'))])
        alerts = []
        driver = login(controller, alert=lambda *args: alerts.append(args))
        with self.assertLogs('youtube_auto_login', level='WARNING'):
            result = driver.sign_in('owner')
        self.assertEqual(result['outcome'], auto._SIDECAR_FAILED)
        self.assertFalse(result['ok'])
        self.assertEqual(len(alerts), 1)

    def test_a_capture_that_never_lands_explains_that_signing_in_was_not_enough(self):
        controller = FakeController([
            ('/google/start', {'outcome': gs.SIGNED_IN}),
            ('/google/capture', {'outcome': gs.CAPTCHA}),
        ])
        result = login(controller, alert=silent).sign_in('owner')
        self.assertFalse(result['ok'])
        self.assertIn('captured', result['detail'].lower())

    def test_a_signed_in_browser_with_no_session_sink_is_still_a_success(self):
        controller = FakeController([('/google/start', {'outcome': gs.SIGNED_IN}),
                                     ('/google/capture', CAPTURED)])
        self.assertTrue(login(controller).sign_in('owner')['ok'])


@requires_sidecar('google_signin.py')
class PageHintTests(unittest.TestCase):
    """What the popup says about the page Google is showing.

    The phone number has to identify the phone without reproducing it, a device
    prompt has to be recognised as a prompt rather than an unknown wall, and the
    security check has to be named as one.
    """

    def test_a_phone_number_is_reduced_to_its_last_four_digits(self):
        for text in ('Enter the code we sent to +1 (555) 010-1234.',
                     'We texted a code to 555-010-1234.',
                     'A code went to +44 20 7946 0958 just now.'):
            with self.subTest(text=text):
                hint = gs.phone_hint(text)
                self.assertTrue(hint.startswith('•••• '), hint)
                self.assertEqual(len(hint.split()[-1]), 4)

    def test_the_masked_number_keeps_no_more_than_four_digits(self):
        hint = gs.phone_hint('A code was sent to +1 (555) 010-1234.')
        self.assertEqual(hint, '•••• 1234')
        self.assertNotIn('555', hint)
        self.assertNotIn('010', hint)

    def test_a_number_google_itself_masks_still_identifies_the_phone(self):
        self.assertEqual(gs.phone_hint('Get a verification code at •••••• •••50'),
                         '•••• 50')
        self.assertEqual(gs.phone_hint('Get a code at •••-•••-1234'), '•••• 1234')
        self.assertEqual(gs.phone_hint('Get a code at •••••• •••88'), '•••• 88')

    def test_a_page_without_a_number_offers_no_hint(self):
        for text in ('Enter the code from your authenticator app.', '',
                     'Google sent a code to your phone.'):
            with self.subTest(text=text):
                self.assertEqual(gs.phone_hint(text), '')

    def test_a_device_prompt_asks_for_the_matching_number(self):
        self.assertEqual(gs.prompt_hint('Tap 42 on your phone to continue.'), '42')
        self.assertEqual(gs.prompt_hint('Open the Gmail app and tap Yes.'), '')

    def test_a_device_prompt_names_the_devices_it_notified(self):
        text = ('Check your device. Google sent a notification to your Galaxy Tab S7+ '
                'and Galaxy S24+. Tap Yes on the notification.')
        self.assertEqual(gs.device_hint(text),
                         'your Galaxy Tab S7+ and Galaxy S24+')
        self.assertEqual(gs.device_hint('Open the Gmail app and tap Yes.'), '')

    def test_a_device_prompt_page_is_a_prompt_not_a_timeout(self):
        for text in ('Tap Yes on your phone or tablet',
                     'Tap 42 on your phone to confirm it is you',
                     'Check your phone and tap the number shown'):
            with self.subTest(text=text):
                state = {'url': 'https://accounts.google.com/v3/signin/challenge/dp',
                         'text': text, 'fields': {}}
                self.assertEqual(gs.classify_page(state), gs.NEED_PROMPT)

    def test_the_chooser_shape_of_the_same_page_is_a_choice(self):
        state = {'url': 'https://accounts.google.com/v3/signin/challenge/dp',
                 'text': 'Choose a 2-step verification method. Try another way',
                 'fields': {}}
        self.assertEqual(gs.classify_page(state), gs.NEED_CHOICE)

    def test_the_chooser_lists_a_passkey_row_without_becoming_a_passkey_demand(self):
        state = {'url': 'https://accounts.google.com/v3/signin/challenge/selection',
                 'text': ('Choose how you want to sign in. Use a passkey. '
                          'Get a verification code at +1 (555) 010-1234. '
                          'Enter one of your backup codes.'),
                 'fields': {}}
        self.assertEqual(gs.classify_page(state), gs.NEED_CHOICE)

    def test_a_failed_text_message_reopens_the_list_instead_of_a_lock(self):
        # When the code cannot be sent, Google prints "We couldn't verify your
        # phone number ... too many failed attempts" above the very same method
        # list. Calling that an account lock hid the ways out still on screen.
        state = {'url': 'https://accounts.google.com/v3/signin/challenge/ipp/error',
                 'text': ('We couldn\u2019t verify your phone number. To help keep '
                          'your account safe, Google wants to make sure it\u2019s really '
                          'you. Too many failed attempts. '
                          'Choose how you want to sign in: Tap Yes on your phone '
                          'or tablet. Use your phone or tablet to get a security code.'),
                 'fields': {}}
        self.assertEqual(gs.classify_page(state), gs.NEED_CHOICE)

    def test_a_real_lock_is_still_a_lock(self):
        state = {'url': 'https://accounts.google.com/v3/signin/challenge/ipp',
                 'text': ('Your account has been temporarily locked because of too '
                          'many failed attempts. Try again later.'),
                 'fields': {}}
        self.assertEqual(gs.classify_page(state), gs.LOCKED)

    def test_the_password_page_is_a_password_page_even_with_its_other_ways_link(self):
        # Google's password screen carries a "Try another way" link. Reading that
        # link first skipped the password entirely and offered verification
        # methods for a session that had not signed in yet.
        state = {'url': 'https://accounts.google.com/v3/signin/challenge/pwd',
                 'text': 'Enter your password. Try another way', 'fields': {}}
        self.assertEqual(gs.classify_page(state), gs.NEED_PASSWORD)
        state['fields'] = {'password': 'input[type=password]'}
        self.assertEqual(gs.classify_page(state), gs.NEED_PASSWORD)

    def test_the_identifier_page_is_not_mistaken_for_a_chooser(self):
        state = {'url': 'https://accounts.google.com/v3/signin/identifier',
                 'text': 'Sign in. Try another way',
                 'fields': {'identifier': 'input[type=email]'}}
        self.assertEqual(gs.classify_page(state), gs.NEED_IDENTIFIER)

    def test_a_device_prompt_wins_over_the_other_ways_link_on_its_page(self):
        state = {'url': 'https://accounts.google.com/v3/signin/challenge/dp',
                 'text': ('Check your device. Google sent a notification to your '
                          'Galaxy S24+. Tap Yes on the notification. Try another way'),
                 'fields': {}}
        self.assertEqual(gs.classify_page(state), gs.NEED_PROMPT)

    def test_the_chooser_on_the_prompt_page_is_a_choice_not_the_prompt_row(self):
        # "Try another way" opens the list on the same /challenge/dp URL, and its
        # first row says "Tap Yes on your phone or tablet". The heading decides.
        state = {'url': 'https://accounts.google.com/v3/signin/challenge/dp',
                 'text': ('Choose how you want to sign in: Tap Yes on your phone or '
                          'tablet. Get a verification code at •••••• •••50. '
                          'Get a verification code from the Google Authenticator app.'),
                 'fields': {}}
        self.assertEqual(gs.classify_page(state), gs.NEED_CHOICE)

    def test_a_refused_password_still_reads_as_a_refusal(self):
        state = {'url': 'https://accounts.google.com/v3/signin/challenge/pwd',
                 'text': "Wrong password. Try again or click 'Forgot password'.",
                 'fields': {'password': 'input[type=password]'}}
        self.assertEqual(gs.classify_page(state), gs.WRONG_PASSWORD)

    def test_an_empty_device_prompt_page_is_not_guessed_at(self):
        state = {'url': 'https://accounts.google.com/v3/signin/challenge/dp',
                 'text': '', 'fields': {}}
        self.assertEqual(gs.classify_page(state), gs.UNSUPPORTED)

    def test_the_three_methods_are_the_only_ones_the_popup_offers(self):
        self.assertEqual(gs.METHODS, ('totp', 'phone', 'backup'))
        for method in gs.METHODS:
            self.assertTrue(gs._METHOD_PATTERNS[method])


@requires_sidecar('google_signin.py')
class SecretHygieneTests(unittest.TestCase):
    def test_each_credential_is_sent_only_to_the_step_that_needs_it(self):
        controller = FakeController([
            ('/google/start', {'outcome': gs.NEED_IDENTIFIER}),
            ('/google/identifier', {'outcome': gs.NEED_PASSWORD}),
            ('/google/password', {'outcome': gs.NEED_TOTP, 'code_kind': 'totp'}),
            ('/google/code', {'outcome': gs.SIGNED_IN}),
            ('/google/capture', CAPTURED),
        ])
        login(controller).sign_in('owner')
        self.assertEqual(controller.stray_secrets(EMAIL, PASSWORD, SECRET, BACKUP[0]), [])
        self.assertEqual(controller.payload('/google/password'),
                         {'profile': 'owner', 'password': PASSWORD})
        self.assertEqual(controller.payload('/google/start'),
                         {'profile': 'owner', 'email': EMAIL})
        # The teardown step always runs and must carry nothing at all.
        self.assertEqual([body for path, _m, body in controller.calls
                          if path == '/google/stop'], [{}])

    def test_the_reported_state_describes_the_credential_without_containing_it(self):
        controller = FakeController([('/google/start', {'outcome': gs.CAPTCHA})])
        driver = login(controller, credential=' '.join(BACKUP), alert=silent)
        driver.sign_in('owner')
        status = driver.status()
        for value in (PASSWORD, SECRET, BACKUP[0], BACKUP[1]):
            self.assertNotIn(value, repr(status))
        self.assertEqual(status['mode'], auto.CODES_MODE)
        self.assertEqual(status['codes_remaining'], 2)
        self.assertEqual(status['email'], EMAIL)
        self.assertTrue(status['configured'])

    def test_the_alert_never_carries_a_credential(self):
        alerts = []
        controller = FakeController([
            ('/google/start', {'outcome': gs.NEED_IDENTIFIER}),
            ('/google/identifier', {'outcome': gs.WRONG_PASSWORD}),
        ])
        login(controller, alert=lambda *args: alerts.append(args)).sign_in('owner')
        self.assertEqual(len(alerts), 1)
        self.assertNotIn(PASSWORD, repr(alerts))
        self.assertNotIn(SECRET, repr(alerts))

    def test_only_the_documented_sidecar_steps_are_ever_called(self):
        controller = FakeController([
            ('/google/start', {'outcome': gs.NEED_IDENTIFIER}),
            ('/google/identifier', {'outcome': gs.NEED_PASSWORD}),
            ('/google/password', {'outcome': gs.NEED_TOTP, 'code_kind': 'totp'}),
            ('/google/code', {'outcome': gs.SIGNED_IN}),
            ('/google/capture', CAPTURED),
        ])
        login(controller).sign_in('owner')
        self.assertLessEqual(set(controller.paths()),
                             {'/google/start', '/google/identifier', '/google/password',
                              '/google/code', '/google/capture', '/google/stop'})


@requires_sidecar('google_signin.py')
class ThrottleTests(unittest.TestCase):
    def test_the_same_failure_is_mailed_once_and_a_new_one_is_mailed_immediately(self):
        alerts = []
        controller = FakeController([('/google/start', {'outcome': gs.CAPTCHA}),
                                     ('/google/start', {'outcome': gs.CAPTCHA}),
                                     ('/google/start', {'outcome': gs.BLOCKED})])
        driver = login(controller, alert=lambda *args: alerts.append(args[0]))
        driver.sign_in()
        driver.sign_in()
        self.assertEqual(alerts, [gs.CAPTCHA])
        driver.sign_in()
        self.assertEqual(alerts, [gs.CAPTCHA, gs.BLOCKED])

    def test_a_dead_sidecar_cannot_become_a_mail_bomb(self):
        alerts = []
        driver = login(FakeController(), alert=lambda *args: alerts.append(args[0]),
                       backoff_base=0.0, alert_cooldown=3600.0)
        for _ in range(20):
            driver.sign_in()
        self.assertEqual(len(alerts), 1)

    def test_a_recovery_resets_the_throttle_so_the_next_outage_is_reported(self):
        driver = login(FakeController(), alert=silent)
        driver.log.mark_alerted(gs.CAPTCHA)
        driver.log.record('signed_in', True)
        self.assertEqual(driver.log.alert_state()[0], '')

    def test_a_server_that_was_never_configured_sends_no_mail(self):
        alerts = []
        login(FakeController(), email='', alert=lambda *args: alerts.append(args)).sign_in()
        login(FakeController(), enabled=False,
              alert=lambda *args: alerts.append(args)).sign_in()
        self.assertEqual(alerts, [])
        self.assertEqual(login(FakeController(), email='').sign_in()['outcome'],
                         auto._NOT_CONFIGURED)
        self.assertEqual(login(FakeController(), enabled=False).sign_in()['outcome'],
                         auto._NOT_CONFIGURED)

    def test_a_broken_mailer_does_not_change_the_sign_in_result(self):
        def explode(*_args):
            raise OSError('smtp down')

        controller = FakeController([('/google/start', {'outcome': gs.CAPTCHA})])
        driver = login(controller, alert=explode)
        with self.assertLogs('youtube_auto_login', level='ERROR'):
            result = driver.sign_in('owner')
        self.assertEqual(result['outcome'], gs.CAPTCHA)
        self.assertFalse(result['ok'])


@requires_sidecar('google_signin.py')
class BackoffTests(unittest.TestCase):
    def test_failures_double_the_wait_up_to_the_cap(self):
        now = [1000.0]
        driver = login(FakeController(), clock=lambda: now[0], alert=silent,
                       backoff_base=900.0, backoff_cap=21600.0)
        waits = []
        for _failure in range(1, 8):
            driver.sign_in('owner')
            waits.append(driver.log.snapshot()['next_attempt_at'] - now[0])
            self.assertFalse(driver.log.due(), 'a retry must not be due straight away')
            now[0] += 900.0
        self.assertEqual(waits[:5], [900.0, 1800.0, 3600.0, 7200.0, 14400.0])
        self.assertTrue(all(wait == 21600.0 for wait in waits[5:]), waits)

    def test_a_transient_failure_is_worth_an_immediate_second_try(self):
        for outcome in ('timeout', auto._SIDECAR_FAILED):
            with self.subTest(outcome=outcome):
                driver = login(FakeController([('/google/start', {'outcome': outcome})]),
                               alert=silent)
                driver.sign_in('owner')
                self.assertEqual(driver.log.snapshot()['last_outcome'], outcome)
                self.assertTrue(driver.log.due())

    def test_a_refused_code_page_is_answered_with_the_next_window_not_abandoned(self):
        # Google can show its own "that code did not work" state instead of leaving
        # the box there. That is a retry signal, and giving up on it would send a
        # human to the desktop for what one more window fixes.
        controller = FakeController([
            ('/google/start', {'outcome': gs.BAD_CODE}),
            ('/google/code', {'outcome': gs.SIGNED_IN}),
            ('/google/capture', CAPTURED),
        ])
        self.assertTrue(login(controller, clock=lambda: 30.0).sign_in('owner')['ok'])
        self.assertEqual(controller.codes_typed(), [auto.totp_at(SECRET, 1)])

    def test_a_failure_that_needs_a_human_waits_instead_of_banging_the_door(self):
        driver = login(FakeController([('/google/start', {'outcome': gs.CAPTCHA})]),
                       alert=silent, backoff_base=900.0)
        driver.sign_in('owner')
        self.assertFalse(driver.log.due())

    def test_a_success_clears_the_backoff(self):
        driver = login(FakeController([('/google/start', {'outcome': gs.SIGNED_IN}),
                                       ('/google/capture', CAPTURED)]),
                       clock=lambda: 5.0)
        driver.log.schedule_retry(999.0)
        self.assertFalse(driver.log.due())
        driver.sign_in('owner')
        self.assertIsNone(driver.log.snapshot()['next_attempt_at'])
        self.assertEqual(driver.log.snapshot()['consecutive_failures'], 0)


@requires_sidecar('google_signin.py')
class SingleFlightTests(unittest.TestCase):
    def test_two_callers_do_not_drive_the_same_browser_at_once(self):
        started = threading.Event()
        release = threading.Event()

        def on_call(path, _payload):
            if path == '/google/start':
                started.set()
                release.wait(timeout=2)

        controller = FakeController([
            ('/google/start', {'outcome': gs.SIGNED_IN}),
            ('/google/capture', CAPTURED),
            ('/google/start', {'outcome': gs.SIGNED_IN}),
            ('/google/capture', CAPTURED),
        ], on_call=on_call)
        driver = login(controller, alert=silent)
        thread = threading.Thread(target=driver.sign_in, args=('owner',))
        thread.start()
        self.assertTrue(started.wait(2), 'the first attempt never reached the sidecar')
        second = driver.sign_in('owner')
        self.assertEqual(second['outcome'], auto._BUSY)
        self.assertFalse(second['ok'])
        release.set()
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive())
        self.assertTrue(driver.sign_in('owner')['ok'], 'the lock was never given back')


if __name__ == '__main__':
    unittest.main()
