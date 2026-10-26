"""The device name a session row shows is one a person can scan.

``describe_user_agent`` is the whole reason /account and the admin session table
read as a list of devices rather than a list of headers, so the two directions it
can fail in are both worth pinning: a real browser must become a name, and a
string that is not a browser must not become a confident wrong answer.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from multiuser import devicename


class DescribeUserAgentTests(unittest.TestCase):
    def label(self, header):
        return devicename.describe_user_agent(header)

    def test_real_browsers_become_a_name_and_a_platform(self):
        cases = {
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/141.0.0.0 Safari/537.36": "Chrome on Linux",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like "
            "Gecko) Chrome/141.0.0.0 Safari/537.36 Edg/141.0.0.0": "Edge on Windows",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, "
            "like Gecko) Chrome/126.0.0.0 Safari/537.36 OPR/106.0.0.0": "Opera on macOS",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:132.0) Gecko/20100101 Firefox/132.0":
            "Firefox on Windows",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:132.0) Gecko/20100101 Firefox/132.0":
            "Firefox on macOS",
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 "
            "(KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1": "Safari on iPhone",
            "Mozilla/5.0 (iPad; CPU OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like "
            "Gecko) CriOS/126.0.6478.72 Mobile/15E148 Safari/604.1": "Chrome on iPad",
            "Mozilla/5.0 (Linux; Android 14; SM-A546B) AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0.6478.188 Mobile Safari/537.36": "Chrome on Android",
            "Mozilla/5.0 (X11; CrOS x86_64 14541.0.0) AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36": "Chrome on ChromeOS",
        }
        for header, expected in cases.items():
            self.assertEqual(self.label(header), expected, header[:60])

    def test_the_label_never_repeats_the_header_it_came_from(self):
        # The point of the column is that it can be read at a glance; a label that
        # still contains the preamble is a label that failed.
        for header in (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/141.0.0.0 Safari/537.36",
            "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
        ):
            self.assertNotIn("Mozilla/5.0", self.label(header))
            self.assertLessEqual(len(self.label(header)), devicename.DEVICE_LABEL_MAX_LEN)

    def test_a_safari_token_on_a_non_apple_platform_is_not_called_safari(self):
        # Webviews, bots and spoofing clients all carry "Safari/537.36" without
        # being Apple's browser; naming one of them Safari would send somebody
        # hunting for a device they do not own.
        self.assertEqual(
            self.label("Mozilla/5.0 (Windows NT 10.0) AppleWebKit/537.36 (KHTML, like "
                       "Gecko) Safari/537.36"),
            "Browser on Windows")

    def test_tools_answer_with_their_own_name(self):
        self.assertEqual(self.label("curl/8.5.0"), "curl")
        self.assertEqual(self.label("python-requests/2.32.3"), "python-requests")
        self.assertEqual(self.label("Werkzeug/3.0.3"), "Werkzeug/3.0.3")

    def test_nothing_recognisable_answers_empty_rather_than_guessing(self):
        for header in ("", None, "   ",
                       "Mozilla/5.0 (some long unrecognisable platform token here that "
                       "goes on for rather too long to be a useful label at all)"):
            self.assertEqual(self.label(header), "", repr(header))

    def test_the_column_cap_cannot_be_exceeded(self):
        # A caller that truncates the header before handing it over must not be
        # able to produce a label longer than the column it is displayed in.
        label = self.label("Mozilla/5.0 (" + "X" * 400 + "; Chrome/1.0 Safari/537.36)")
        self.assertLessEqual(len(label), devicename.DEVICE_LABEL_MAX_LEN)


if __name__ == "__main__":
    unittest.main()
