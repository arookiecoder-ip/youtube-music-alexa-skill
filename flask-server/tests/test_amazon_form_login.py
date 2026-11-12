"""Every page Amazon can leave the scripted sign-in on, mapped to a form state.

Amazon accounts differ: one is signed in by email, one by mobile number, one
uses an authenticator, one gets a one-time password by text or email, one has
no second step at all, and one is stopped by a captcha or a "too many attempts"
page. The state machine is what decides which question Music Box asks next, so
each alexapy status flag is pinned to exactly one answer here.
"""
import ast
import re
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

SOURCE = Path(__file__).resolve().parents[1] / "alexa_remote.py"


def _nodes():
    module = ast.parse(SOURCE.read_text())
    functions = {n.name: n for n in module.body if isinstance(n, ast.FunctionDef)}
    remote = next(n for n in module.body
                  if isinstance(n, ast.ClassDef) and n.name == "AlexaRemote")
    methods = {n.name: n for n in remote.body
               if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef))}
    return functions, methods


def _namespace():
    functions, methods = _nodes()
    chosen = [functions["_parse_choice_options"], functions["_normalize_identifier"],
              functions["_is_intent_url"], functions["_is_intent_page"],
              functions["_last_page_url"], functions["_robot_check_form"],
              methods["_form_state"], methods["_password_page_pending"]]
    namespace = {"re": re, "staticmethod": staticmethod}
    exec(compile(ast.Module(body=chosen, type_ignores=[]), str(SOURCE), "exec"),
         namespace)
    return namespace


class AmazonFormStateTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.ns = _namespace()
        self.owner = SimpleNamespace(_form_captcha=None)
        self.owner._form_fetch_captcha = AsyncMock(
            return_value=("image/png", b"png-bytes"))

    async def state(self, **status):
        return await self.ns["_form_state"](
            self.owner, SimpleNamespace(status=status))

    async def test_a_login_with_no_second_step_is_connected(self):
        self.assertEqual(await self.state(login_successful=True), {"state": "connected"})

    async def test_two_step_code_names_its_sources_and_keeps_amazons_error(self):
        state = await self.state(securitycode_required=True)
        self.assertEqual(state["state"], "need_code")
        self.assertEqual(state["answer"], "securitycode")
        self.assertEqual(state["label"], "Two-step verification code")
        self.assertIn("authenticator", state["hint"])
        self.assertIn("backup", state["hint"])
        refused = await self.state(securitycode_required=True,
                                   error_message="The code was incorrect.")
        self.assertEqual(refused["message"], "The code was incorrect.")
        self.assertEqual(refused["label"], "Two-step verification code")

    async def test_one_time_password_is_labelled_apart_from_two_step(self):
        state = await self.state(verificationcode_required=True)
        self.assertEqual(state["state"], "need_code")
        self.assertEqual(state["answer"], "verificationcode")
        self.assertEqual(state["label"], "One-time password")
        self.assertIn("phone number or email", state["hint"])

    async def test_verification_captcha_keeps_the_picture_and_its_flag(self):
        login = SimpleNamespace(status={"captcha_required": True,
                                        "captcha_image_url": "/errors/validateCaptcha",
                                        "verification_captcha_required": True})
        state = await self.ns["_form_state"](self.owner, login)
        self.assertEqual(state["state"], "need_captcha")
        self.assertTrue(state["text"])
        self.assertTrue(state["verification"])
        self.assertEqual(self.owner._form_captcha, ("image/png", b"png-bytes"))
        self.owner._form_fetch_captcha.assert_awaited_once_with(
            login, "/errors/validateCaptcha")

    async def test_a_login_captcha_is_not_a_verification_captcha(self):
        state = await self.state(captcha_required=True)
        self.assertEqual(state["state"], "need_captcha")
        self.assertFalse(state["verification"])

    async def test_an_unloadable_captcha_ends_the_attempt_honestly(self):
        self.owner._form_fetch_captcha = AsyncMock(return_value=None)
        state = await self.state(captcha_required=True)
        self.assertEqual(state["state"], "error")
        self.assertIn("captcha", state["message"])

    async def test_choosing_how_to_verify_lists_the_methods(self):
        state = await self.state(
            claimspicker_required=True,
            claimspicker_message="* **`0`**:\t `a1 - Text a code to +91 90000 00000`.\n"
                                 "* **`1`**:\t `a2 - Use an authenticator app`.\n")
        self.assertEqual(state["state"], "need_choice")
        self.assertEqual(state["kind"], "claimsoption")
        self.assertEqual(state["message"], "Choose how Amazon should verify it's you.")
        self.assertEqual(state["options"],
                         [{"index": "0", "label": "Text a code to +91 90000 00000"},
                          {"index": "1", "label": "Use an authenticator app"}])

    async def test_choosing_where_to_send_the_one_time_password(self):
        state = await self.state(
            authselect_required=True,
            authselect_message="0:\tSend to +91 90000 00000\n"
                               "1:\tSend to o***@example.test\n")
        self.assertEqual(state["kind"], "authselectoption")
        self.assertEqual(state["message"],
                         "Choose where Amazon should send your one-time password.")
        self.assertEqual([option["label"] for option in state["options"]],
                         ["Send to +91 90000 00000", "Send to o***@example.test"])

    async def test_an_unparsed_chooser_still_hands_over_amazons_list(self):
        state = await self.state(claimspicker_required=True,
                                 claimspicker_message="unexpected layout")
        self.assertNotIn("options", state)
        self.assertEqual(state["raw"], "unexpected layout")

    async def test_an_amazon_error_note_overrides_the_chooser_prompt(self):
        state = await self.state(authselect_required=True,
                                 error_message="That number is no longer available.")
        self.assertEqual(state["message"], "That number is no longer available.")

    async def test_device_approval_waits_for_the_person(self):
        state = await self.state(action_required=True,
                                 message="Approve the sign-in in the Amazon app.")
        self.assertEqual(state["state"], "need_approval")
        self.assertEqual(state["message"], "Approve the sign-in in the Amazon app.")
        self.assertFalse(state["approved"])

    async def test_the_polling_page_reports_a_finished_approval(self):
        state = await self.state(approval_status="TransactionCompleted")
        self.assertEqual(state["state"], "need_approval")
        self.assertTrue(state["approved"])
        self.assertIn("Approve", state["message"])

    async def test_too_many_attempts_points_at_amazons_own_page(self):
        state = await self.state(login_failed="forgot_password")
        self.assertEqual(state["state"], "error")
        self.assertIn("Amazon's own page", state["message"])
        self.assertNotIn("password", state["message"].lower())

    async def test_an_amazon_page_this_form_cannot_answer_says_so(self):
        state = await self.state(ap_error=True)
        self.assertEqual(state["state"], "error")
        self.assertIn("cannot answer", state["message"])

    async def test_refused_credentials_mention_the_mobile_number_too(self):
        state = await self.state(login_failed="login_failed")
        self.assertIn("email or mobile number", state["message"])

    async def test_empty_mfa_page_does_not_blame_credentials(self):
        login = SimpleNamespace(status={'login_failed':'login_failed'},
                                _page_url='https://www.amazon.in/ap/mfa?private=not-logged',
                                _page_html='', _page_status=302)
        state = await self.ns['_form_state'](self.owner, login)
        self.assertEqual(state['code'], 'amazon_mfa_page_unavailable')
        self.assertIn('verification page', state['message'])
        self.assertNotIn('password', state['message'])

    async def test_amazons_register_page_means_the_identifier_was_not_found(self):
        login = SimpleNamespace(
            status={"login_failed": "login_failed"},
            _lastreq=SimpleNamespace(url="https://www.amazon.in/ax/claim/intent?arb=1"))
        state = await self.ns["_form_state"](self.owner, login)
        self.assertEqual(state["state"], "error")
        self.assertIn("doesn't recognise", state["message"])
        self.assertNotIn("password", state["message"].lower())

    async def test_the_page_kept_on_the_login_survives_its_session_reset(self):
        # AlexaPy clears _lastreq when it resets the session after a failed
        # confirmation, so the page it read is kept apart from the response.
        login = SimpleNamespace(
            status={"login_failed": "login_failed"}, _lastreq=None,
            _page_url="https://www.amazon.com/ax/claim/intent?arb=2")
        state = await self.ns["_form_state"](self.owner, login)
        self.assertIn("doesn't recognise", state["message"])

    async def test_amazons_own_error_text_wins_over_a_generic_guess(self):
        state = await self.state(login_failed="login_failed",
                                 error_message="We cannot find an account with that email address.")
        self.assertEqual(state["message"],
                         "We cannot find an account with that email address.")

    async def test_an_unrecognised_page_is_an_error_not_a_dead_end(self):
        state = await self.state()
        self.assertEqual(state["state"], "error")
        self.assertIn("cannot answer", state["message"])


ROBOT_CHECK_PAGE = """<html><body>
  <form action="/errors_page/validateCaptcha" method="post">
    <input type="hidden" name="amzn" value="a1b2">
    <input type="hidden" name="amzn-r" value="/ap/signin?openid.mode=checkid_setup">
    <input type="text" name="field-keywords">
    <input type="submit" name="submit">
    <img src="/errors/validateCaptcha?amzn=a1b2&amp;amzn-r=%2Fap%2Fsignin">
  </form>
</body></html>"""


class AmazonRobotCheckTests(unittest.IsolatedAsyncioTestCase):
    """Amazon's site-wide robot check is not a sign-in step, but it is answerable."""

    def setUp(self):
        self.ns = _namespace()
        self.owner = SimpleNamespace(_form_captcha=None, _form_robot=None)
        self.owner._form_fetch_captcha = AsyncMock(
            return_value=("image/jpeg", b"jpeg-bytes"))

    def test_the_challenge_form_carries_its_fields_and_picture(self):
        login = SimpleNamespace(_page_html=ROBOT_CHECK_PAGE,
                                _page_url="https://www.amazon.com/errors_page/validateCaptcha")
        form = self.ns["_robot_check_form"](login)
        self.assertEqual(form["action"], "/errors_page/validateCaptcha")
        self.assertEqual(form["fields"]["amzn"], "a1b2")
        self.assertIn("field-keywords", form["fields"])
        self.assertTrue(form["image"].startswith("/errors/validateCaptcha"))

    def test_a_sign_in_page_is_not_the_challenge(self):
        login = SimpleNamespace(
            _page_html='<form name="signIn" action="/ax/claim">'
                       '<input name="email"><input name="password"></form>')
        self.assertIsNone(self.ns["_robot_check_form"](login))

    async def test_the_picture_is_shown_and_the_signed_in_flow_continues(self):
        login = SimpleNamespace(
            status={"login_failed": "login_failed"},
            _page_html=ROBOT_CHECK_PAGE,
            _page_url="https://www.amazon.com/errors_page/validateCaptcha")
        state = await self.ns["_form_state"](self.owner, login)
        self.assertEqual(state["state"], "need_captcha")
        self.assertEqual(state["answer"], "captcha")
        self.assertTrue(state["text"])
        self.assertIn("person", state["message"])
        self.assertEqual(self.owner._form_captcha, ("image/jpeg", b"jpeg-bytes"))
        self.assertEqual(self.owner._form_robot["action"], "/errors_page/validateCaptcha")

    async def test_an_unloadable_picture_does_not_pose_as_a_password_problem(self):
        self.owner._form_fetch_captcha = AsyncMock(return_value=None)
        login = SimpleNamespace(
            status={"login_failed": "login_failed"},
            _page_html=ROBOT_CHECK_PAGE,
            _page_url="https://www.amazon.com/errors_page/validateCaptcha")
        state = await self.ns["_form_state"](self.owner, login)
        self.assertEqual(state["state"], "error")
        self.assertNotIn("password", state["message"].lower())


class AmazonSplitPasswordTests(unittest.TestCase):
    """Amazon sometimes asks for the identifier, then a password on its own page."""

    def setUp(self):
        self.pending = _namespace()["_password_page_pending"]

    def login(self, status, fields):
        return SimpleNamespace(status=status, _data=fields)

    def test_an_unfilled_password_page_is_answered_without_asking_again(self):
        self.assertTrue(self.pending(self.login({}, {"email": "", "password": ""})))

    def test_a_page_without_a_password_field_is_not_that_flow(self):
        self.assertFalse(self.pending(self.login({}, {"email": ""})))

    def test_a_second_factor_page_is_not_that_flow(self):
        self.assertFalse(self.pending(
            self.login({"securitycode_required": True}, {"password": ""})))

    def test_a_refused_password_is_not_retried(self):
        self.assertFalse(self.pending(
            self.login({"login_failed": "login_failed",
                        "error_message": "Your password is incorrect."},
                       {"password": ""})))

    def test_a_finished_login_is_not_that_flow(self):
        self.assertFalse(self.pending(
            self.login({"login_successful": True}, {"password": ""})))


class AmazonIdentifierTests(unittest.TestCase):
    def setUp(self):
        self.normalize = _namespace()["_normalize_identifier"]

    def test_an_email_is_left_exactly_as_typed(self):
        self.assertEqual(self.normalize(" Owner@Example.test "), "Owner@Example.test")

    def test_a_formatted_mobile_number_is_reduced_to_digits(self):
        self.assertEqual(self.normalize("+91 90000-00000"), "+919000000000")
        self.assertEqual(self.normalize("+91 (90000) 00000"), "+919000000000")
        self.assertEqual(self.normalize("90000 00000"), "9000000000")

    def test_something_that_is_neither_is_passed_through(self):
        self.assertEqual(self.normalize("not a number"), "not a number")
        self.assertEqual(self.normalize(""), "")


class AmazonChoiceParsingTests(unittest.TestCase):
    def setUp(self):
        self.parse = _namespace()["_parse_choice_options"]

    def test_claimspicker_lines_keep_only_the_readable_label(self):
        self.assertEqual(
            self.parse("* **`0`**:\t `a1 - Email a code`.\n"),
            [{"index": "0", "label": "Email a code"}])

    def test_authselect_lines_read_as_numbered_destinations(self):
        self.assertEqual(
            self.parse("0:\tSend to a***@gmail.com\n1:\tSend to +91 90000 00000\n"),
            [{"index": "0", "label": "Send to a***@gmail.com"},
             {"index": "1", "label": "Send to +91 90000 00000"}])

    def test_lines_without_an_option_are_ignored(self):
        self.assertEqual(self.parse("Nothing to choose here."), [])


class AmazonAttemptRaceTests(unittest.IsolatedAsyncioTestCase):
    async def test_stale_answer_and_cancel_cannot_touch_replacement(self):
        _, methods = _nodes()
        namespace = {}
        exec(compile(ast.Module(body=[methods['_form_login_answer'], methods['_cancel_form_attempt']], type_ignores=[]), str(SOURCE), 'exec'), namespace)
        owner = SimpleNamespace(_form_attempt_id='new-attempt', _form_login=object(),
                                _claim_proxy=AsyncMock(), _form_cleanup=AsyncMock(),
                                _form_step=AsyncMock())
        reply = await namespace['_form_login_answer'](owner, 'securitycode', '123456', 'old-attempt')
        self.assertEqual(reply['state'], 'error')
        self.assertFalse(await namespace['_cancel_form_attempt'](owner, 'old-attempt'))
        owner._claim_proxy.assert_not_awaited()
        owner._form_cleanup.assert_not_awaited()
        owner._form_step.assert_not_awaited()


class AmazonPromotionCleanupTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_promotion_closes_only_pending_session_and_removes_stage(self):
        import asyncio, shutil, tempfile
        from unittest.mock import Mock
        _, methods = _nodes()
        namespace={'asyncio':asyncio,'shutil':shutil,'logger':Mock(),
                   '_renew_proxy_claim':Mock(),'_release_proxy_claim':Mock()}
        exec(compile(ast.Module(body=[methods['_form_step'],methods['_form_cleanup']],type_ignores=[]),str(SOURCE),'exec'),namespace)
        with tempfile.TemporaryDirectory() as root:
            stage=Path(root)/'pending';stage.mkdir();(stage/'cookie').write_text('pending cookie')
            live=Path(root)/'live-cookie';live.write_text('previous working session')
            old=SimpleNamespace(close=AsyncMock())
            login=SimpleNamespace(login=AsyncMock(),close=AsyncMock())
            owner=SimpleNamespace(_form_login=login,_form_stage=str(stage),_form_attempt_id='attempt',
                FORM_STEP_TIMEOUT=1,_login=old,_form_state=AsyncMock(return_value={'state':'connected'}),
                _promote_staged_login=AsyncMock(return_value='Identity verification failed.'))
            owner._form_cleanup=namespace['_form_cleanup'].__get__(owner)
            result=await namespace['_form_step'](owner,{'securitycode':'synthetic'})
            self.assertEqual(result['state'],'error')
            login.close.assert_awaited_once();old.close.assert_not_awaited()
            self.assertIsNone(owner._form_login);self.assertIsNone(owner._form_stage)
            self.assertFalse(stage.exists());self.assertEqual(live.read_text(),'previous working session')
