import tempfile
import unittest
from types import SimpleNamespace
from error_alerts import ErrorAlerts
from status_journal import Journal


class ErrorAlertTests(unittest.TestCase):
    def setUp(self):
        self.sent = []
        self.alerts = ErrorAlerts(lambda subject, text: self.sent.append((subject, text)) or SimpleNamespace(status='sent'), start=False)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.journal = Journal(self.tmp.name + '/logs.db', start=False, secrets=lambda: ('synthetic-secret',))
        self.journal.error_callback = self.alerts.submit

    def test_errors_include_context_and_trace_but_not_credentials_or_urls(self):
        self.journal.record('ERROR', 'resolver', 'failed synthetic-secret https://example.test/private?q=value',
            'ValueError: invalid response\nresolver.py:12 in resolve', request_id='a'*32, route='/audio/')
        self.alerts.flush()
        body = self.sent[0][1]
        for expected in ('ERROR', 'resolver', 'ValueError', 'resolver.py:12', 'a'*32, '/audio/', 'UTC'):
            self.assertIn(expected, body)
        for secret in ('synthetic-secret', 'example.test', 'q=value'):
            self.assertNotIn(secret, body)

    def test_normal_logs_do_not_send_and_duplicate_errors_are_counted(self):
        self.journal.record('INFO', 'playback', 'normal')
        self.journal.record('WARNING', 'playback', 'warning')
        self.alerts.flush()
        self.assertEqual(self.sent, [])
        for _ in range(20):
            self.journal.record('ERROR', 'playback', 'connection failed')
        self.alerts.flush()
        self.assertEqual(len(self.sent), 1)
        self.assertIn('20 occurrence(s)', self.sent[0][1])
        self.alerts.flush()
        self.assertEqual(len(self.sent), 1)

    def test_delivery_failure_keeps_errors_without_recursion_and_retries(self):
        self.journal.record('CRITICAL', 'worker', 'worker crashed')
        self.alerts.deliver = lambda *args: SimpleNamespace(status='error')
        self.alerts.flush()
        self.assertEqual(self.alerts.failures, 1)
        self.assertEqual(len(self.alerts.groups), 1)
        self.alerts.deliver = lambda subject, text: self.sent.append(text) or SimpleNamespace(status='sent')
        self.alerts.flush()
        self.assertEqual(len(self.sent), 1)
        self.assertFalse(self.alerts.groups)

    def test_queue_pressure_is_bounded_and_reported(self):
        self.alerts = ErrorAlerts(lambda subject, text: self.sent.append(text) or SimpleNamespace(status='sent'), capacity=1, start=False)
        self.journal.error_callback = self.alerts.submit
        for _ in range(4): self.journal.record('ERROR', 'test', 'failed')
        self.assertEqual(self.alerts.pending.qsize(), 1)
        self.alerts.flush()
        self.assertIn('3 additional errors', self.sent[0])

    def test_library_error_logs_are_alerted_while_library_info_is_quiet(self):
        import logging
        from status_journal import JournalHandler
        handler = JournalHandler(lambda: self.journal)
        handler.emit(logging.LogRecord('urllib3.connectionpool', logging.INFO, '', 0, 'normal retry', (), None))
        handler.emit(logging.LogRecord('urllib3.connectionpool', logging.ERROR, '', 0, 'upstream failed', (), None))
        self.alerts.flush()
        self.assertEqual(len(self.sent), 1)
        self.assertIn('upstream failed', self.sent[0][1])
