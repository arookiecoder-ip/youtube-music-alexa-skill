"""The second factor at both doors, and the guessing budget they now share.

Sign-in is two steps for any identity that carries an authenticator: the legacy
owner's ``REMOTE_TOTP_SECRET`` and a multi-user account's own enrolled secret go
through the same pending-token mechanism at ``/login/``, and the administrator
door at ``/admin/login/`` enforces the same rule so an admin's 2FA is not
decoration.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from cryptography.fernet import Fernet
from flask import Flask

from multiuser import init_multiuser
from multiuser import netids
from multiuser.pending import PendingSecondFactor, bind_value
from multiuser.security import totp_at

PLAYER_UA = "Mozilla/5.0 (player browser)"
ADMIN_UA = "Mozilla/5.0 (admin browser)"
# Both doors derive their address the same way, so one XFF value at each is the
# same client and therefore the same budget.
CLIENT_IP = "203.0.113.50"
PLAYER_PASSWORD = "Player!Passw0rd"
# The legacy single-owner credentials, forced on the door rather than inherited
# from whichever environment happened to import ``server`` first -- and a name no
# multi-user account can collide with, so the account path never shadows it.
LEGACY_USER = "legacy-boss"
LEGACY_PASSWORD = "Legacy!Passw0rd"

_SERVER = None


def _load_server():
    """``server.py`` with its heavy collaborators stubbed, as the other login tests do."""
    global _SERVER
    if _SERVER is None:
        from tests.test_session_cookie_config import _install_stubs

        _install_stubs()
        # Import is when ``server.py`` reads DB_FILE and starts its background loops,
        # so an unguarded import inside a running container would point those loops at
        # the live install's database -- and one of them issues real yt-dlp calls that
        # land in whichever test is patching ``subprocess.run`` at that moment.
        with mock.patch.dict(os.environ, {"DB_FILE": SERVER_DB}):
            import server

        _SERVER = server
    return _SERVER


def pending_value(html: str) -> str:
    marker = 'name="pending" value="'
    start = html.index(marker) + len(marker)
    return html[start:html.index('"', start)]


# One SQLite file for server.py's own tables, for the whole module: it outlives the
# per-test directories, which is where its ``web_sessions`` rows have to go.
_SERVER_TMP = tempfile.mkdtemp(prefix="second-factor-server-")
SERVER_DB = os.path.join(_SERVER_TMP, "server.db")


def tearDownModule():
    shutil.rmtree(_SERVER_TMP, ignore_errors=True)


class PendingStoreTests(unittest.TestCase):
    """The mechanism itself: single use, expiring, bound to a browser."""

    def setUp(self):
        self.store = PendingSecondFactor(ttl_seconds=60, capacity=4)
        self.bind = bind_value(CLIENT_IP, "some browser")

    def test_a_record_is_returned_once_and_never_again(self):
        token = self.store.issue({"kind": "owner", "user": "owner"}, self.bind)
        self.assertEqual(self.store.consume(token, self.bind)["user"], "owner")
        self.assertIsNone(self.store.consume(token, self.bind))

    def test_an_unknown_or_empty_token_is_simply_nothing(self):
        self.store.issue({"kind": "owner"}, self.bind)
        self.assertIsNone(self.store.consume("not-a-token", self.bind))
        self.assertIsNone(self.store.consume("", self.bind))
        self.assertIsNone(self.store.consume(None, self.bind))

    def test_the_fingerprint_of_another_browser_cannot_spend_it(self):
        token = self.store.issue({"kind": "account", "uid": "u1"}, self.bind)
        self.assertIsNone(self.store.consume(token, bind_value("198.51.100.4", "some browser")))
        # Consumed even by the refusal: whoever holds the token next gets nothing.
        self.assertIsNone(self.store.consume(token, self.bind))

    def test_an_aged_out_record_dies(self):
        token = self.store.issue({"kind": "owner"}, self.bind)
        self.store._records[token]["at"] = time.time() - 61
        self.assertIsNone(self.store.consume(token, self.bind))

    def test_the_store_is_bounded_so_waiting_browsers_cannot_grow_it(self):
        for index in range(10):
            self.store.issue({"kind": "owner", "n": index}, self.bind)
        self.assertLessEqual(self.store.waiting(), 4)
        self.assertGreater(self.store.waiting(), 0)

    def test_the_fingerprint_hides_the_address_it_was_made_from(self):
        self.assertNotIn(CLIENT_IP, bind_value(CLIENT_IP, "browser"))


class SecondFactorDoorTestBase(unittest.TestCase):
    """One layer instance, reachable from both doors."""

    admin_password = "Owner!Passw0rd"

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="second-factor-")
        env = {
            "MULTI_USER_ENABLED": "1",
            "DB_FILE": os.path.join(self.tmp, "data.db"),
            "SECRET_KEY": "test-secret-key",
            "MULTIUSER_ADMIN_USER": "owner",
            "MULTIUSER_ADMIN_PASSWORD": self.admin_password,
            "MULTIUSER_ADMIN_EMAIL": "owner@synthora.in",
            "MULTIUSER_ENCRYPTION_KEY": Fernet.generate_key().decode("ascii"),
            "MULTIUSER_MAIL_OUTBOX": os.path.join(self.tmp, "outbox"),
            # Blanked, not merely unset: the real MULTIUSER_SMTP_* of a deployed
            # machine would otherwise send every one-time link over the network and
            # leave the outbox these tests read empty.
            "MULTIUSER_SMTP_HOST": "",
            "MULTIUSER_SMTP_USER": "",
            "MULTIUSER_MAIL_FROM": "",
            "MULTIUSER_PUBLIC_BASE_URL": "http://localhost",
            "MULTIUSER_REQUIRE_EMAIL_VERIFICATION": "0",
            # Small enough that exhausting a budget is three requests, not fifty.
            "MULTIUSER_LOGIN_MAX_ATTEMPTS": "3",
        }
        self.env_patch = mock.patch.dict(os.environ, env, clear=False)
        self.env_patch.start()
        # Registered against a throwaway app: the doors only use the layer's
        # services, so server.py's own app keeps exactly its own routes.
        self.layer_app = Flask("second-factor-layer")
        self.layer_app.secret_key = "layer-session-secret"
        self.layer = init_multiuser(self.layer_app)
        self.assertIsNotNone(self.layer)
        self.accounts = self.layer.accounts
        self.owner = self.accounts.get_user_by_username("owner")
        self.admin_client = self.layer_app.test_client()

    def tearDown(self):
        self.env_patch.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ----------------------------------------------------------------- helpers
    def enroll(self, username: str, password: str) -> str:
        user = self.accounts.get_user_by_username(username)
        secret = self.accounts.begin_totp_enrollment(
            user, current_password=password, ip="127.0.0.1", user_agent="enroller",
        )["secret"]
        self.accounts.confirm_totp_enrollment(
            user, code=totp_at(secret, int(time.time()) // 30),
            ip="127.0.0.1", user_agent="enroller",
        )
        # Confirming spends the 30-second window it was confirmed in, by design: one
        # code cannot both arm the factor and open a session. A test that enrols and
        # signs in seconds later would collide with its own enrollment, so move that
        # spend into the past rather than wait on the clock.
        self.accounts.db.execute(
            "UPDATE users SET totp_last_step = 0 WHERE username = ?", (username,)
        )
        return secret

    def player_user(self, username: str, password: str = PLAYER_PASSWORD) -> dict:
        return self.accounts.create_user(
            self.owner, username, password, email=f"{username}@example.test",
            role="user", must_change_password=False, email_verified=True,
        )

    def exhaust(self, scope: str, key: str) -> None:
        """Spend a whole budget for one key, at the size both doors give that scope.

        The address budget is deliberately ten times the per-account one -- one
        household behind one NAT should not lock itself out -- so "spent" means
        different counts per scope, and the door's own formula lives here.
        """
        limit = self.layer.config.login_max_attempts
        if scope == "login-ip":
            limit *= 10
        window = self.layer.config.login_window_seconds
        for _ in range(limit):
            self.layer.rate_limiter.allow(scope, key, limit, window)

    def admin_csrf(self) -> str:
        self.admin_client.get("/admin/login/")
        with self.admin_client.session_transaction() as session:
            return session.get("mu_csrf", "")

    def admin_step1(self, username: str, password: str, *, ua: str = ADMIN_UA):
        return self.admin_client.post(
            "/admin/login/",
            data={"_csrf": self.admin_csrf(), "username": username, "password": password},
            headers={"User-Agent": ua, "X-Forwarded-For": CLIENT_IP},
        )

    def admin_step2(self, pending: str, code: str, *, ua: str = ADMIN_UA,
                    ip: str = CLIENT_IP):
        return self.admin_client.post(
            "/admin/login/",
            data={"_csrf": self.admin_csrf(), "step": "code", "pending": pending, "code": code},
            headers={"User-Agent": ua, "X-Forwarded-For": ip},
        )

    def bind_player_door(self):
        """Point ``server.py``'s login route at this test's layer and credentials."""
        server = _load_server()
        # server.py keeps its own SQLite file from import time, and every test here
        # gets a fresh temp directory: without repointing it, the second test in the
        # module writes ``web_sessions`` into a file that no longer exists. The file
        # is shared across this module, but ``_db_initialized`` is server.py's
        # process-wide flag and another module may already have raised it, so the
        # schema is asserted here rather than trusted to run order.
        patches = [
            mock.patch.object(server, "_multiuser_layer", self.layer),
            mock.patch.object(server, "_remote_login_enabled", return_value=True),
            mock.patch.object(server, "REMOTE_USER", LEGACY_USER),
            mock.patch.object(server, "REMOTE_PASSWORD", LEGACY_PASSWORD),
            mock.patch.object(server, "_totp_enabled", return_value=False),
            mock.patch.object(server, "DB_FILE", SERVER_DB),
            mock.patch.object(server, "_db_initialized", False),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.server = server
        self.client = server.app.test_client()
        return self.client


class AdminDoorTests(SecondFactorDoorTestBase):
    def test_an_admin_without_a_factor_signs_in_in_one_step(self):
        response = self.admin_step1("owner", self.admin_password)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin/", response.headers["Location"])

    def test_the_password_alone_only_buys_a_code_box(self):
        self.enroll("owner", self.admin_password)
        first = self.admin_step1("owner", self.admin_password)
        self.assertEqual(first.status_code, 200, "no session and no redirect yet")
        body = first.get_data(as_text=True)
        self.assertIn('name="pending"', body)
        self.assertIn("Authentication code", body)
        self.assertNotIn('name="password"', body, "step two must not re-ask for the password")
        self.assertEqual(self.admin_client.get("/admin/").status_code, 302)

    def test_the_code_finishes_the_sign_in(self):
        secret = self.enroll("owner", self.admin_password)
        pending = pending_value(self.admin_step1("owner", self.admin_password).get_data(as_text=True))
        second = self.admin_step2(pending, totp_at(secret, int(time.time()) // 30))
        self.assertEqual(second.status_code, 302, second.get_data(as_text=True)[:400])
        self.assertEqual(self.admin_client.get("/admin/").status_code, 200)

    def test_a_wrong_code_ends_the_attempt_rather_than_offering_another_guess(self):
        secret = self.enroll("owner", self.admin_password)
        pending = pending_value(self.admin_step1("owner", self.admin_password).get_data(as_text=True))
        wrong = "000000" if totp_at(secret, int(time.time()) // 30) != "000000" else "111111"
        self.assertEqual(self.admin_step2(pending, wrong).status_code, 401)
        # The good code is worthless now: the proof behind it was spent.
        self.assertEqual(
            self.admin_step2(pending, totp_at(secret, int(time.time()) // 30)).status_code, 401
        )
        self.assertEqual(self.admin_client.get("/admin/").status_code, 302)

    def test_a_code_issued_to_one_browser_cannot_be_spent_from_another(self):
        secret = self.enroll("owner", self.admin_password)
        pending = pending_value(self.admin_step1("owner", self.admin_password).get_data(as_text=True))
        stolen = self.admin_step2(
            pending, totp_at(secret, int(time.time()) // 30),
            ua="Mozilla/5.0 (another machine)", ip="198.51.100.77",
        )
        self.assertEqual(stolen.status_code, 401)

    def test_an_admin_demoted_between_the_steps_cannot_finish(self):
        secret = self.enroll("owner", self.admin_password)
        pending = pending_value(self.admin_step1("owner", self.admin_password).get_data(as_text=True))
        # A second owner is needed: nobody may demote or disable the only account
        # that can undo it, so that guard would refuse the setup this test is about.
        second = self.accounts.create_user(
            self.owner, "second", "Second!Passw0rd", role="owner", must_change_password=False,
        )
        self.accounts.update_user(second, self.owner["id"], {"role": "user"})
        response = self.admin_step2(pending, totp_at(secret, int(time.time()) // 30))
        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.admin_client.get("/admin/").status_code, 302)

    def test_a_disabled_account_cannot_finish_either(self):
        secret = self.enroll("owner", self.admin_password)
        pending = pending_value(self.admin_step1("owner", self.admin_password).get_data(as_text=True))
        second = self.accounts.create_user(
            self.owner, "second", "Second!Passw0rd", role="owner", must_change_password=False,
        )
        self.accounts.set_status(second, self.owner["id"], "disabled")
        response = self.admin_step2(pending, totp_at(secret, int(time.time()) // 30))
        self.assertEqual(response.status_code, 401)

    def test_the_admin_door_never_grants_a_plain_user_a_session(self):
        self.player_user("player1")
        self.assertEqual(self.admin_step1("player1", PLAYER_PASSWORD).status_code, 403)


class SharedLoginBudgetTests(SecondFactorDoorTestBase):
    """``/login/`` and ``/admin/login/`` spend the same two budgets."""

    def setUp(self):
        super().setUp()
        self.player_client = self.bind_player_door()
        self.client = self.player_client
        self.player_user("player1")

    def player_step1(self, username: str, password: str, *, ua: str = PLAYER_UA,
                     ip: str = CLIENT_IP):
        return self.player_client.post(
            "/login/", json={"username": username, "password": password},
            headers={"User-Agent": ua, "X-Forwarded-For": ip},
        )

    def test_a_budget_spent_at_one_door_is_spent_at_the_other(self):
        # Same scope string and same bucket function at both doors is the design;
        # this is what stops either door from quietly getting its own allowance.
        self.exhaust("login-account", netids.identifier_bucket("player1"))
        admin = self.admin_step1("player1", PLAYER_PASSWORD)
        self.assertEqual(admin.status_code, 429)
        player = self.player_step1("player1", PLAYER_PASSWORD)
        self.assertEqual(player.status_code, 429)
        self.assertIn("Retry-After", player.headers)
        self.assertEqual(player.get_json()["error"]["code"], "rate_limited")
        other = self.player_step1("owner", self.admin_password)
        self.assertEqual(other.status_code, 200, "the budget is per identity, not per server")

    def test_the_address_budget_is_shared_the_same_way(self):
        self.exhaust("login-ip", netids.ip_bucket(CLIENT_IP))
        self.assertEqual(self.player_step1("owner", self.admin_password).status_code, 429)
        self.assertEqual(self.admin_step1("owner", self.admin_password).status_code, 429)
        elsewhere = self.player_step1("owner", self.admin_password, ip="198.51.100.9")
        self.assertEqual(elsewhere.status_code, 200)

    def test_a_real_sign_in_clears_the_budget_the_other_door_is_using(self):
        # Regression: the reset passed the raw username and address instead of the
        # bucketed keys allow() had spent, so it was a silent no-op.
        limit = self.layer.config.login_max_attempts
        for _ in range(limit - 1):
            self.assertEqual(self.admin_step1("player1", "wrong password").status_code, 401)
        self.assertEqual(self.player_step1("player1", PLAYER_PASSWORD).status_code, 200)
        self.assertEqual(self.admin_step1("player1", "wrong password").status_code, 401,
                         "the budget was cleared, so this is attempt one again")

    def test_the_budget_also_covers_the_right_password(self):
        # 429 must be about *how often*, never about whether the guess was good:
        # a correct answer after the budget is spent is still refused.
        self.exhaust("login-account", netids.identifier_bucket("player1"))
        refused = self.player_step1("player1", PLAYER_PASSWORD)
        self.assertEqual(refused.status_code, 429)
        with self.player_client.session_transaction() as session:
            self.assertIsNone(session.get("mu_sid"))

    def test_a_locked_account_is_still_refused_while_the_budget_holds(self):
        for _ in range(self.layer.config.login_max_attempts):
            self.player_step1("player1", "wrong password")
        self.assertNotEqual(
            self.player_step1("player1", PLAYER_PASSWORD).status_code, 200
        )


class PlayerDoorTests(SecondFactorDoorTestBase):
    def setUp(self):
        super().setUp()
        self.bind_player_door()

    def post(self, **payload):
        headers = {"User-Agent": PLAYER_UA, "X-Forwarded-For": CLIENT_IP}
        return self.client.post("/login/", json=payload, headers=headers)

    def test_an_account_without_a_factor_is_signed_in_at_once(self):
        self.player_user("player1")
        response = self.post(username="player1", password=PLAYER_PASSWORD)
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True)[:300])
        self.assertFalse(response.get_json().get("totp_required"))
        with self.client.session_transaction() as session:
            self.assertTrue(session.get("mu_sid"))
            self.assertIsNone(session.get("remote_user"))

    def test_the_two_step_sign_in_mints_the_session_the_one_step_would(self):
        self.player_user("player1")
        secret = self.enroll("player1", PLAYER_PASSWORD)
        first = self.post(username="player1", password=PLAYER_PASSWORD)
        self.assertEqual(first.status_code, 200)
        self.assertTrue(first.get_json()["totp_required"])
        with self.client.session_transaction() as session:
            self.assertIsNone(session.get("mu_sid"), "no session before the code")
        second = self.post(step="totp", token=first.get_json()["token"],
                           code=totp_at(secret, int(time.time()) // 30))
        self.assertEqual(second.status_code, 200, second.get_data(as_text=True)[:300])
        with self.client.session_transaction() as session:
            self.assertTrue(session.get("mu_sid"))
            self.assertIsNone(session.get("remote_user"))

    def test_a_number_is_accepted_for_the_code_a_json_body_may_send(self):
        self.player_user("player1")
        secret = self.enroll("player1", PLAYER_PASSWORD)
        token = self.post(username="player1", password=PLAYER_PASSWORD).get_json()["token"]
        code = int(totp_at(secret, int(time.time()) // 30))
        response = self.post(step="totp", token=token, code=code)
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True)[:300])

    def test_a_pending_enrollment_is_not_a_second_factor(self):
        # Otherwise arming 2FA without confirming it would lock the owner out of
        # their own account, and hand an attacker a way to do exactly that.
        self.player_user("player1")
        user = self.accounts.get_user_by_username("player1")
        self.accounts.begin_totp_enrollment(
            user, current_password=PLAYER_PASSWORD, ip="127.0.0.1", user_agent="x"
        )
        response = self.post(username="player1", password=PLAYER_PASSWORD)
        self.assertFalse(response.get_json().get("totp_required"))

    def test_a_wrong_code_spends_the_proof_and_a_replay_starts_over(self):
        self.player_user("player1")
        secret = self.enroll("player1", PLAYER_PASSWORD)
        token = self.post(username="player1", password=PLAYER_PASSWORD).get_json()["token"]
        bad = "000000" if totp_at(secret, int(time.time()) // 30) != "000000" else "111111"
        self.assertEqual(self.post(step="totp", token=token, code=bad).status_code, 401)
        replay = self.post(step="totp", token=token, code=totp_at(secret, int(time.time()) // 30))
        self.assertEqual(replay.status_code, 401)
        self.assertIn("start again", replay.get_json()["error"]["message"])
        with self.client.session_transaction() as session:
            self.assertIsNone(session.get("mu_sid"))

    def test_a_token_stolen_from_another_browser_cannot_sign_in(self):
        self.player_user("player1")
        secret = self.enroll("player1", PLAYER_PASSWORD)
        token = self.post(username="player1", password=PLAYER_PASSWORD).get_json()["token"]
        thief = self.client.post(
            "/login/",
            json={"step": "totp", "token": token, "code": totp_at(secret, int(time.time()) // 30)},
            headers={"User-Agent": "Mozilla/5.0 (a different machine)",
                     "X-Forwarded-For": "198.51.100.9"},
        )
        self.assertEqual(thief.status_code, 401)
        with self.client.session_transaction() as session:
            self.assertIsNone(session.get("mu_sid"))

    def test_an_account_disabled_between_the_two_steps_is_not_signed_in(self):
        self.player_user("player1")
        secret = self.enroll("player1", PLAYER_PASSWORD)
        token = self.post(username="player1", password=PLAYER_PASSWORD).get_json()["token"]
        user = self.accounts.get_user_by_username("player1")
        self.accounts.set_status(self.owner, user["id"], "disabled")
        response = self.post(step="totp", token=token, code=totp_at(secret, int(time.time()) // 30))
        self.assertEqual(response.status_code, 401)
        with self.client.session_transaction() as session:
            self.assertIsNone(session.get("mu_sid"))

    def test_an_account_whose_factor_was_turned_off_between_the_steps_starts_over(self):
        self.player_user("player1")
        secret = self.enroll("player1", PLAYER_PASSWORD)
        token = self.post(username="player1", password=PLAYER_PASSWORD).get_json()["token"]
        user = self.accounts.get_user_by_username("player1")
        self.accounts.disable_totp(
            user, current_password=PLAYER_PASSWORD,
            code=totp_at(secret, int(time.time()) // 30), ip="127.0.0.1", user_agent="x"
        )
        response = self.post(step="totp", token=token, code=totp_at(secret, int(time.time()) // 30))
        self.assertEqual(response.status_code, 401)

    def test_the_owner_branch_only_answers_for_the_owner(self):
        # A record for an account must never fall through to the legacy identity,
        # and a code the *owner's* secret accepts is not an account's answer.
        self.player_user("player1")
        self.enroll("player1", PLAYER_PASSWORD)
        token = self.post(username="player1", password=PLAYER_PASSWORD).get_json()["token"]
        with mock.patch.object(self.server, "_totp_verify", return_value=True):
            response = self.post(step="totp", token=token, code="111111")
        self.assertEqual(response.status_code, 401)
        with self.client.session_transaction() as session:
            self.assertIsNone(session.get("remote_user"))
            self.assertIsNone(session.get("mu_sid"))

    def test_the_legacy_owner_still_uses_its_own_secret(self):
        with mock.patch.object(self.server, "_totp_enabled", return_value=True), \
                mock.patch.object(self.server, "_totp_verify",
                                  side_effect=lambda code, window=1: code == "654321"):
            first = self.post(username=self.server.REMOTE_USER, password=self.server.REMOTE_PASSWORD)
            self.assertEqual(first.status_code, 200, first.get_data(as_text=True)[:300])
            self.assertTrue(first.get_json()["totp_required"])
            token = first.get_json()["token"]
            self.assertEqual(self.post(step="totp", token=token, code="111111").status_code, 401)
            self.assertEqual(
                self.post(step="totp", token=token, code="654321").status_code, 401,
                "the wrong code spent the proof, so the right one must start over",
            )
            again = self.post(username=self.server.REMOTE_USER, password=self.server.REMOTE_PASSWORD)
            done = self.post(step="totp", token=again.get_json()["token"], code="654321")
            self.assertEqual(done.status_code, 200, done.get_data(as_text=True)[:300])
            with self.client.session_transaction() as session:
                self.assertEqual(session.get("remote_user"), self.server.REMOTE_USER)
                self.assertIsNone(session.get("mu_sid"))

    def test_the_page_carries_the_code_step_and_the_account_links(self):
        page = self.client.get("/login/", headers={"User-Agent": PLAYER_UA}).get_data(as_text=True)
        self.assertIn('id="step-code"', page, "step two must exist even when the owner has no 2FA")
        self.assertIn("/signup", page)
        self.assertIn("/forgot-password", page)

    def test_the_signup_link_follows_the_registration_door(self):
        with mock.patch.object(self.accounts, "registration_open", lambda: False):
            page = self.client.get("/login/", headers={"User-Agent": PLAYER_UA}).get_data(as_text=True)
        self.assertNotIn("/signup", page)
        self.assertIn("/forgot-password", page, "recovery still works while sign-ups are shut")

    def test_an_expired_pending_record_dies_on_its_own(self):
        self.player_user("player1")
        secret = self.enroll("player1", PLAYER_PASSWORD)
        token = self.post(username="player1", password=PLAYER_PASSWORD).get_json()["token"]
        self.server._pending_2fa._records[token]["at"] = time.time() - self.server._PENDING_TTL - 1
        response = self.post(step="totp", token=token, code=totp_at(secret, int(time.time()) // 30))
        self.assertEqual(response.status_code, 401)


class SingleOwnerDoorTests(unittest.TestCase):
    def test_a_installation_without_the_layer_keeps_its_own_two_step_login(self):
        server = _load_server()
        client = server.app.test_client()
        secret = "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP"
        code = totp_at(secret, int(time.time()) // 30)
        with mock.patch.object(server, "_multiuser_layer", None), \
                mock.patch.object(server, "_remote_login_enabled", return_value=True), \
                mock.patch.object(server, "REMOTE_USER", LEGACY_USER), \
                mock.patch.object(server, "REMOTE_PASSWORD", LEGACY_PASSWORD), \
                mock.patch.object(server, "DB_FILE", SERVER_DB), \
                mock.patch.object(server, "_db_initialized", False), \
                mock.patch.object(server, "_totp_enabled", return_value=True), \
                mock.patch.object(server, "_totp_verify",
                                  side_effect=lambda value, window=1: value == code):
            # Read the page before the sign-in: once this browser holds a session
            # the door redirects instead of rendering.
            page = client.get("/login/").get_data(as_text=True)
            first = client.post("/login/", json={
                "username": LEGACY_USER, "password": LEGACY_PASSWORD})
            self.assertTrue(first.get_json()["totp_required"])
            second = client.post("/login/", json={
                "step": "totp", "token": first.get_json()["token"], "code": code,
                "username": LEGACY_USER, "password": LEGACY_PASSWORD})
            self.assertEqual(second.status_code, 200, second.get_data(as_text=True)[:300])
        self.assertIn('id="step-code"', page, "the code step is in the document either way")
        self.assertNotIn("/signup", page, "no account door exists to link at")


if __name__ == "__main__":
    unittest.main()
