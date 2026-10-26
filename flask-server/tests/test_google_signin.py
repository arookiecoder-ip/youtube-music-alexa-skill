"""Every shape Google's sign-in form can take, classified without a browser.

The automated login is only as safe as its reading of the page: mistaking a
phone prompt for a code box spends a credential on nonsense, and mistaking a
captcha for a timeout keeps knocking on a door that will never open. Each case
below is a snapshot of what the browser could see, taken from Google's own
challenge paths and error copy.
"""
import importlib.util
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

_SIDECAR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'browser-auth'))


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(_SIDECAR, filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# This is the sidecar's own Google ladder, so it only runs where browser-auth
# ships: the host checkout and the sidecar image, never the application image.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sidecar_support import BROWSER_AUTH, requires_sidecar  # noqa: E402

gs = _load('google_signin_under_test', 'google_signin.py') if (BROWSER_AUTH / 'google_signin.py').is_file() else None
import youtube_auto_login as auto  # noqa: E402


def snapshot(url, text='', fields=None):
    return {'url': url, 'text': text, 'fields': fields or {}}


IDENTIFIER = snapshot(
    'https://accounts.google.com/v3/signin/identifier',
    'Find your email\nEnter your email or phone number',
    {'identifier': 'input#identifier'})
PASSWORD = snapshot(
    'https://accounts.google.com/v3/signin/challenge/pwd',
    'Welcome\nEnter your password',
    {'password': 'input#password'})


@requires_sidecar('google_signin.py')
class IdentifierStepTests(unittest.TestCase):
    def test_the_email_screen_asks_for_an_email(self):
        self.assertEqual(gs.classify_page(IDENTIFIER), gs.NEED_IDENTIFIER)

    def test_the_password_screen_asks_for_a_password(self):
        self.assertEqual(gs.classify_page(PASSWORD), gs.NEED_PASSWORD)

    def test_a_bounced_back_browser_is_signed_in(self):
        self.assertEqual(gs.classify_page(
            snapshot('https://music.youtube.com/library/songs', 'Library')), gs.SIGNED_IN)

    def test_a_youtube_oauth_landing_is_not_read_as_a_login(self):
        self.assertEqual(gs.classify_page(
            snapshot('https://music.youtube.com/serviceauth', '')), gs.UNSUPPORTED)


@requires_sidecar('google_signin.py')
class TwoStepTests(unittest.TestCase):
    def test_authenticator_challenge(self):
        self.assertEqual(gs.classify_page(snapshot(
            'https://accounts.google.com/signin/challenge/totp/2',
            'Enter your authenticator code',
            {'code': 'input#totpPin', 'code_kind': 'totp'})), gs.NEED_TOTP)

    def test_backup_code_challenge(self):
        self.assertEqual(gs.classify_page(snapshot(
            'https://accounts.google.com/signin/challenge/backup/3',
            'Enter one of your 8-digit backup codes',
            {'code': 'input#backupCodePin', 'code_kind': 'backup'})), gs.NEED_BACKUP_CODE)

    def test_an_unnamed_code_box_is_read_from_its_own_copy(self):
        self.assertEqual(gs.classify_page(snapshot(
            'https://accounts.google.com/signin/challenge/unknown',
            'Enter the backup code you saved',
            {'code': "input[inputmode=numeric]", 'code_kind': 'generic'})),
            gs.NEED_BACKUP_CODE)
        self.assertEqual(gs.classify_page(snapshot(
            'https://accounts.google.com/signin/challenge/unknown',
            'Enter the 6-digit code from your app',
            {'code': "input[inputmode=numeric]", 'code_kind': 'generic'})), gs.NEED_TOTP)

    def test_only_a_machine_answerable_challenge_is_answered(self):
        self.assertEqual(gs.ANSWERABLE, (gs.NEED_TOTP, gs.NEED_BACKUP_CODE))
        for url, text, expected in (
            ('https://accounts.google.com/signin/challenge/skip',
             'Choose a 2-step verification method', gs.NEED_CHOICE),
            ('https://accounts.google.com/signin/challenge/prompt/5',
             'Tap "Send now" on your phone', gs.NEED_PROMPT),
            ('https://accounts.google.com/signin/challenge/ipp/7',
             'Enter the code we texted you', gs.NEED_PHONE),
            ('https://accounts.google.com/signin/challenge/emb/43',
             'Use your passkey to finish signing in', gs.NEED_PASSKEY),
            ('https://accounts.google.com/signin/challenge/az/1',
             'Tap your phone to approve', gs.NEED_PASSKEY),
        ):
            with self.subTest(url=url):
                self.assertEqual(gs.classify_page(snapshot(url, text)), expected)

    def test_the_phone_and_passkey_challenges_need_a_human(self):
        for outcome in (gs.NEED_CHOICE, gs.NEED_PROMPT, gs.NEED_PHONE, gs.NEED_PASSKEY,
                        gs.CAPTCHA, gs.BLOCKED, gs.LOCKED, gs.UNKNOWN_ACCOUNT,
                        gs.WRONG_PASSWORD):
            self.assertIn(outcome, gs.NEEDS_HUMAN, f'{outcome} has no manual way out')
            self.assertIn(outcome, auto.HUMAN_OUTCOMES,
                          f'{outcome} is human-only in the browser but not to the app')

    def test_an_unrecognised_challenge_stops_rather_than_guesses(self):
        # Clicking the wrong thing here can spend a backup code or trip a lockout.
        self.assertEqual(gs.classify_page(snapshot(
            'https://accounts.google.com/signin/challenge/weird/9',
            'Something entirely new')), gs.UNSUPPORTED)


@requires_sidecar('google_signin.py')
class FailureTests(unittest.TestCase):
    def test_wrong_password_beats_the_password_field_it_sits_on(self):
        self.assertEqual(gs.classify_page(snapshot(
            'https://accounts.google.com/v3/signin/challenge/pwd',
            'Wrong password. Try again or click Forgot password to reset it.',
            {'password': 'input#password'})), gs.WRONG_PASSWORD)

    def test_a_rejected_code_stays_on_the_challenge_it_belongs_to(self):
        # The page still shows the code box, so the caller must know which
        # challenge to answer again rather than being told "bad code" with no
        # idea whether it was the TOTP or the backup code that failed.
        self.assertEqual(gs.classify_page(snapshot(
            'https://accounts.google.com/signin/challenge/totp/2',
            "That code didn't work. Check the code and try again.",
            {'code': 'input#totpPin', 'code_kind': 'totp'})), gs.NEED_TOTP)

    def test_unknown_account_and_lockout_are_told_apart(self):
        self.assertEqual(gs.classify_page(snapshot(
            'https://accounts.google.com/v3/signin/identifier',
            "Couldn't find your Google Account")), gs.UNKNOWN_ACCOUNT)
        self.assertEqual(gs.classify_page(snapshot(
            'https://accounts.google.com/v3/signin/identifier',
            'Too many failed attempts. Try again later.')), gs.LOCKED)

    def test_the_sorry_page_is_a_captcha_even_without_the_word(self):
        self.assertEqual(gs.classify_page(snapshot(
            'https://accounts.google.com/sorry/index?continue=https://music.youtube.com/',
            'Unusual traffic from your computer network')), gs.CAPTCHA)

    def test_googles_browser_refusal_is_its_own_state(self):
        self.assertEqual(gs.classify_page(snapshot(
            'https://accounts.google.com/v3/signin/block',
            'This browser or app may not be secure.')), gs.BLOCKED)

    def test_a_consent_wall_is_its_own_state(self):
        self.assertEqual(gs.classify_page(snapshot(
            'https://consent.youtube.com/glwt', 'Before you continue to YouTube')),
            gs.CONSENT)

    def test_a_page_with_nothing_to_act_on_is_unsupported(self):
        self.assertEqual(gs.classify_page(snapshot('about:blank', '')), gs.UNSUPPORTED)
        self.assertEqual(gs.classify_page(snapshot(None, '')), gs.UNSUPPORTED)
        self.assertEqual(gs.classify_page({}), gs.UNSUPPORTED)


class FakePage:
    """A page that records what it was asked to do instead of rendering it."""

    class Locator:
        def __init__(self, page, selector):
            self.page, self.selector = page, selector

        @property
        def first(self):
            return self

        def is_visible(self):
            return self.selector in self.page.visible

        def click(self, **kwargs):
            self.page.actions.append(('click', self.selector))

        def press_sequentially(self, value, delay=0):
            self.page.actions.append(('type', self.selector, value))

    def __init__(self, url, text='', visible=()):
        self.url = url
        self.text = text
        self.visible = list(visible)
        self.actions = []

    def locator(self, selector):
        return FakePage.Locator(self, selector)

    def inner_text(self, selector, timeout=0):
        return self.text

    def get_by_text(self, *args, **kwargs):
        raise LookupError('no such text')

    @property
    def keyboard(self):
        return self

    def press(self, key):
        self.actions.append(('key', key))


@requires_sidecar('google_signin.py')
class DriverTests(unittest.TestCase):
    def test_the_identifier_is_typed_into_the_field_the_page_actually_has(self):
        page = FakePage(IDENTIFIER['url'], IDENTIFIER['text'], ['input#identifier'])
        gs.submit_identifier(page, 'someone@gmail.com', wait_seconds=0)
        # _type focuses before it types, so the click is expected; what must not
        # vary is which selector the address landed in.
        self.assertIn(('type', 'input#identifier', 'someone@gmail.com'), page.actions)

    def test_a_missing_field_is_reported_rather_than_typed_into_anything(self):
        page = FakePage('https://accounts.google.com/signin/challenge/weird', 'Nothing here')
        state, outcome = gs.submit_password(page, 'hunter2')
        self.assertEqual(outcome, gs.UNSUPPORTED)
        self.assertEqual(page.actions, [])

    def test_a_snapshot_carries_the_shape_of_the_page_and_never_what_was_typed(self):
        page = FakePage(PASSWORD['url'], PASSWORD['text'], ['input#password'])
        gs.submit_password(page, 'hunter2', wait_seconds=0)
        state = gs.read_page_state(page)
        self.assertEqual(state['fields']['password'], 'input#password')
        self.assertNotIn('hunter2', repr(state))


@requires_sidecar('google_signin.py')
class OperatorCopyTests(unittest.TestCase):
    def test_every_outcome_the_classifier_can_produce_explains_itself(self):
        """The classifier and the caller's copy table must not drift apart.

        An outcome with no sentence becomes a blank alert, which is worse than no
        alert: it reads as if the server had recovered.
        """
        produced = sorted(value for name, value in vars(gs).items()
                          if name.isupper() and isinstance(value, str)
                          and not value.startswith(('http', '/')))
        self.assertTrue(produced)
        for outcome in produced:
            if outcome == gs.SIGNED_IN:
                continue  # a success has nothing for the operator to fix
            self.assertIn(outcome, auto._COPY, f'{outcome} has no operator-facing sentence')


if __name__ == '__main__':
    unittest.main()
