"""Client addressing for the abuse controls.

The whole point of this module is that a visitor must not be able to choose which
rate-limit bucket they land in, so every test here is a question about who is
allowed to be believed.
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from flask import Flask

from multiuser import netids


class NetidsTest(unittest.TestCase):
    # An operator who deployed this behind Cloudflare would have the flag in the
    # environment of every test run, so "declared" here means declared by the
    # test -- never inherited from the machine running it.
    BLANK = {"MULTIUSER_TRUSTED_PROXY_CIDRS": "", "MULTIUSER_TRUST_CLOUDFLARE": ""}

    def setUp(self):
        self.app = Flask(__name__)
        netids._cached_raw = None
        netids._cached_settings = netids.Settings((), False)

    def tearDown(self):
        netids._cached_raw = None
        netids._cached_settings = netids.Settings((), False)

    def probe(self, remote_addr, headers=None, env=None):
        with mock.patch.dict(os.environ, {**self.BLANK, **(env or {})}):
            with self.app.test_request_context(
                "/", headers=headers or {}, environ_base={"REMOTE_ADDR": remote_addr}
            ) as ctx:
                return netids.client_ip(ctx.request)

    # -------------------------------------------------------------- who is believed
    def test_public_peer_is_never_convinced_by_forwarded_for(self):
        # An attacker on the open internet cannot make their own peer private.
        # (The TEST-NET documentation ranges would not prove this: Python counts
        # them as private, precisely because they are not routable either.)
        self.assertEqual(
            self.probe("8.8.4.4", {"X-Forwarded-For": "10.0.0.1"}),
            "8.8.4.4",
        )

    def test_private_peer_uses_the_rightmost_forwarded_address(self):
        # Caddy appends the real peer to whatever the client sent, so the last
        # entry is the only one our own proxy wrote.
        chain = "198.51.100.7, 203.0.113.44"
        self.assertEqual(
            self.probe("172.18.0.4", {"X-Forwarded-For": chain}),
            "203.0.113.44",
        )

    def test_garbage_in_the_chain_falls_back_to_the_peer(self):
        self.assertEqual(self.probe("172.18.0.4", {"X-Forwarded-For": "not-an-ip"}), "172.18.0.4")

    def test_cloudflare_header_is_ignored_unless_declared(self):
        headers = {"CF-Connecting-IP": "198.51.100.1"}
        self.assertEqual(self.probe("172.18.0.4", headers), "172.18.0.4")
        self.assertEqual(
            self.probe("172.18.0.4", headers, {"MULTIUSER_TRUST_CLOUDFLARE": "1"}),
            "198.51.100.1",
        )

    def test_declaring_cloudflare_does_not_trust_the_whole_internet(self):
        # Someone reaching the origin directly bypasses Cloudflare and can type
        # anything into CF-Connecting-IP; the flag must not hand them the key.
        self.assertEqual(
            self.probe("8.8.4.4", {"CF-Connecting-IP": "198.51.100.1"},
                       {"MULTIUSER_TRUST_CLOUDFLARE": "1"}),
            "8.8.4.4",
        )

    def test_a_declared_cloudflare_fallthrough_still_uses_the_forwarded_chain(self):
        # No CF header (a non-Cloudflare client at the same edge): the ordinary
        # rightmost rule still applies rather than returning the proxy.
        self.assertEqual(
            self.probe("172.18.0.4",
                       {"X-Forwarded-For": "198.51.100.7, 203.0.113.44"},
                       {"MULTIUSER_TRUST_CLOUDFLARE": "1"}),
            "203.0.113.44",
        )

    def test_configured_proxy_cidr_overrides_the_private_heuristic(self):
        # An address outside every private range is only believed once named.
        self.assertEqual(self.probe("8.8.4.4", {"X-Forwarded-For": "203.0.113.5"}), "8.8.4.4")
        self.assertEqual(
            self.probe(
                "8.8.4.4",
                {"X-Forwarded-For": "203.0.113.5"},
                {"MULTIUSER_TRUSTED_PROXY_CIDRS": "8.8.0.0/16"},
            ),
            "203.0.113.5",
        )

    def test_unparsable_proxy_cidr_does_not_open_the_door(self):
        self.assertEqual(
            self.probe("8.8.4.4", {"X-Forwarded-For": "203.0.113.5"},
                       {"MULTIUSER_TRUSTED_PROXY_CIDRS": "garbage"}),
            "8.8.4.4",
        )

    def test_a_named_proxy_list_replaces_the_heuristic_rather_than_extending_it(self):
        # Worth pinning down: listing one proxy means every other peer, private
        # included, is answered with its own socket address.
        self.assertEqual(
            self.probe("172.18.0.4", {"X-Forwarded-For": "203.0.113.5"},
                       {"MULTIUSER_TRUSTED_PROXY_CIDRS": "8.8.0.0/16"}),
            "172.18.0.4",
        )

    def test_forwarded_entry_with_a_port_is_normalised(self):
        self.assertEqual(
            self.probe("10.0.0.2", {"X-Forwarded-For": "[2001:db8::1]:4321"}),
            "2001:db8::1",
        )

    # ------------------------------------------------------------------- bucketing
    def test_ipv6_neighbours_share_one_bucket(self):
        left = netids.ip_bucket("2001:db8:1234:5678:1111:2222:3333:4444")
        right = netids.ip_bucket("2001:db8:1234:5678:9999:aaaa:bbbb:cccc")
        self.assertEqual(left, right)
        self.assertNotEqual(left, netids.ip_bucket("2001:db8:1234:5679::1"))

    def test_ipv4_bucket_is_the_address_itself(self):
        self.assertEqual(netids.ip_bucket("203.0.113.9"), "203.0.113.9")

    def test_identifier_bucket_hides_the_identity_and_folds_case(self):
        key = netids.identifier_bucket("  Sanam12341@Example.COM ")
        self.assertEqual(key, netids.identifier_bucket("sanam12341@example.com"))
        self.assertEqual(len(key), 32)
        self.assertNotIn("sanam", key.lower())

    def test_empty_and_junk_keys_stay_usable(self):
        self.assertEqual(netids.ip_bucket(""), "unknown")
        self.assertEqual(netids.ip_bucket("::gg::"), "::gg::"[:64])
        self.assertTrue(netids.identifier_bucket(""))

    def test_headers_full_of_junk_never_reach_the_stored_key(self):
        # A truncated address would be a *valid* address in a limiter's eyes, so
        # the guard is: only something that parses is ever believed.
        self.assertEqual(
            self.probe("10.0.0.1",
                       {"CF-Connecting-IP": "203.0.113.7" + "0" * 80,
                        "X-Forwarded-For": "198.51.100.1"},
                       {"MULTIUSER_TRUST_CLOUDFLARE": "1"}),
            "198.51.100.1",
        )

    def test_user_agent_is_capped(self):
        with self.app.test_request_context(
            "/", headers={"User-Agent": "x" * 900}
        ) as ctx:
            self.assertEqual(len(netids.user_agent(ctx.request)), netids.USER_AGENT_MAX_LEN)


class ServerForwardedChainTests(unittest.TestCase):
    """Whether the WSGI server lets the forwarded chain reach Flask at all.

    waitress 3 deletes X-Forwarded-For from the environ unless its own
    ``trusted_proxy`` option names the exact proxy address, and a docker network
    hands Caddy an ephemeral one. Nothing above the server layer can recover a
    header the server already dropped, so the deployment wiring is worth pinning:
    with it wrong, every visitor shares one rate-limit bucket and no amount of
    care inside netids changes that.
    """

    def test_the_launcher_leaves_the_forwarded_chain_intact(self):
        root = Path(__file__).resolve().parents[1]
        self.assertIn('"clear_untrusted_proxy_headers": False',
                      (root / "serve.py").read_text())

    def test_the_image_starts_that_launcher_and_not_the_cli(self):
        dockerfile = (Path(__file__).resolve().parents[1] / "Dockerfile").read_text()
        self.assertIn("serve.py", dockerfile)
        self.assertNotIn("waitress-serve", dockerfile)


if __name__ == "__main__":
    unittest.main()
