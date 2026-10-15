import json
import logging
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock
from status_journal import Journal, JournalHandler, RETENTION, redact


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)/'logs.sqlite3'
        self.now = 2000000000.
        self.journal = Journal(self.path, secrets=lambda: ('private-key-value',), clock=lambda:self.now, start=False)
        self.journal.drain()

    def record(self, message, level='INFO'):
        self.now += .01
        self.journal.record(level, 'fixture', message)

    def test_redaction_handles_known_secrets_multiline_auth_urls_paths_and_identity(self):
        for value in ['authorization: Bearer abc\nDEF', '{"api_key":"test"}', 'client_secret=private', 'cookie: SID=one\ntwo']:
            self.assertIn('omitted', redact(value))
        text=redact('Failed private-key-value at https://upstream.example/path?key=unknown on /app/private/file.py user@example.com serial=G01234', ['private-key-value'])
        for secret in ['private-key-value','upstream.example','unknown','/app/private','user@example','G01234']:
            self.assertNotIn(secret,text)
        self.assertEqual(2048,len(redact('x'*10000)))

    def test_seen_survives_restart_and_only_explicit_ids_are_acknowledged(self):
        self.record('first failure','ERROR'); self.journal.drain()
        first=self.journal.query(errors=True)['entries'][0]
        self.record('new failure','ERROR'); self.journal.drain()
        self.assertEqual(2,self.journal.summary()['unseen'])
        self.assertEqual(1,self.journal.seen([first['id']])['unseen'])
        self.assertEqual(1,self.journal.seen([first['id']])['unseen'])
        reopened=Journal(self.path,clock=lambda:self.now,start=False); reopened.drain()
        self.assertEqual(1,reopened.summary()['unseen'])
        self.assertTrue(reopened.detail(first['id'])['entry']['seen'])

    def test_exact_24_hour_expiry_hidden_immediately_and_deleted_on_cleanup(self):
        self.record('expired failure','ERROR'); self.journal.drain()
        error=self.journal.query(errors=True)['entries'][0]
        self.now=error['at']+RETENTION
        self.assertEqual(0,self.journal.summary()['unseen'])
        self.assertEqual([],self.journal.query()['entries'])
        self.assertIsNone(self.journal.detail(error['id']))
        self.journal.drain()
        with sqlite3.connect(self.path) as conn: self.assertEqual(0,conn.execute('select count(*) from logs').fetchone()[0])

    def test_context_bounded_and_opening_does_not_mark_seen(self):
        for i in range(25): self.record(f'before {i}')
        self.record('failure','ERROR')
        for i in range(25): self.record(f'after {i}')
        self.journal.drain()
        error=self.journal.query(errors=True)['entries'][0]
        detail=self.journal.detail(error['id'])
        self.assertEqual((10,10),(len(detail['earlier']),len(detail['later'])))
        self.assertEqual('before 24',detail['earlier'][-1]['message'])
        self.assertEqual('after 0',detail['later'][0]['message'])
        self.assertEqual(1,self.journal.summary()['unseen'])

    def test_search_and_filters_literal_sql_safe_with_stable_pagination(self):
        self.record('contains % underscore_ literal')
        self.record('second','WARNING'); self.record('third','ERROR'); self.journal.drain()
        self.assertEqual(1,len(self.journal.query(search='%')['entries']))
        self.assertEqual([],self.journal.query(search="' OR 1=1 --")['entries'])
        self.assertEqual('second',self.journal.query(level='WARNING',source='fixture')['entries'][0]['message'])
        page=self.journal.query(limit=2)
        self.record('new arrival'); self.journal.drain()
        older=self.journal.query(before=page['next_before'],limit=2)
        self.assertEqual(['contains % underscore_ literal'],[row['message'] for row in older['entries']])

    def test_independent_caps_protect_errors_from_info_bursts(self):
        journal=Journal(self.path,clock=lambda:self.now,start=False,max_logs=5,max_errors=3)
        for i in range(10): journal.record('ERROR','fixture',f'error {i}')
        for i in range(50): journal.record('INFO','fixture',f'info {i}')
        journal.drain()
        self.assertEqual(3,journal.summary()['unseen'])
        self.assertEqual(8,len(journal.query()['entries']))
        self.assertEqual(['error 9','error 8','error 7'],[row['message'] for row in journal.query(errors=True)['entries']])
        for i in range(50): journal.record('INFO','fixture',f'new info {i}')
        journal.drain(); self.assertEqual(3,journal.summary()['unseen'])

    def test_queue_is_bounded_nonblocking_and_producers_never_touch_sqlite(self):
        with mock.patch.object(self.journal,'_connect',side_effect=AssertionError('producer touched disk')):
            for i in range(1000): self.journal.record('INFO','fixture',f'entry {i}')
            for i in range(300): self.journal.record('ERROR','fixture',f'error {i}')
        self.assertEqual(512,self.journal.normal.qsize()); self.assertEqual(128,self.journal.errors.qsize())
        self.assertEqual(660,self.journal.dropped)

    def test_concurrent_producers_keep_valid_records_and_account_for_drops(self):
        threads=[threading.Thread(target=lambda:[self.journal.record('INFO','fixture','entry') for _ in range(100)]) for _ in range(8)]
        for t in threads:t.start()
        for t in threads:t.join()
        self.assertEqual(800,self.journal.normal.qsize()+self.journal.dropped)
        while not self.journal.normal.empty():self.journal.drain()
        self.assertEqual(512,len(self.journal.query(limit=1000)['entries']))

    def test_disk_failure_does_not_escape_to_producer_and_is_visible(self):
        self.record('entry')
        with mock.patch.object(self.journal,'_connect',side_effect=sqlite3.OperationalError('read-only')):
            with self.assertRaises(sqlite3.OperationalError):self.journal.drain()
            self.record('another entry')
        self.assertFalse(self.journal.available); self.assertGreater(self.journal.dropped,0)
        self.journal.drain(); self.assertTrue(self.journal.available)

    def test_watchdog_condition_logs_once_until_resolved_then_can_recur(self):
        for _ in range(5):self.journal.condition('disk',True,'Low free disk space')
        self.journal.drain(); self.assertEqual(1,self.journal.summary()['unseen'])
        self.journal.condition('disk',False,''); self.journal.condition('disk',True,'Low free disk space')
        self.journal.drain(); self.assertEqual(2,self.journal.summary()['unseen'])

    def test_handler_captures_exception_type_and_trace_without_credentials(self):
        handler=JournalHandler(lambda:self.journal)
        try:raise ValueError('private-key-value')
        except ValueError:
            import sys
            record=logging.LogRecord('fixture',logging.ERROR,__file__,1,'Unable to decode audio',(),sys.exc_info())
        handler.emit(record); self.journal.drain()
        row=self.journal.query(errors=True)['entries'][0]
        self.assertIn('ValueError',row['trace']); self.assertNotIn('private-key-value',json.dumps(row))
        self.assertEqual(0o600,self.path.stat().st_mode & 0o777)

    def test_error_retention_is_independent_from_seen_state(self):
        self.record('failure','ERROR');self.journal.drain()
        error=self.journal.query(errors=True)['entries'][0]
        self.journal.seen([error['id']]);self.assertIsNotNone(self.journal.detail(error['id']))
        self.now+=RETENTION+1;self.journal.drain();self.assertIsNone(self.journal.detail(error['id']))

    def test_background_worker_flushes_without_explicit_drain(self):
        import time
        journal=Journal(Path(self.temp.name)/'background.sqlite3')
        try:
            journal.record('ERROR','fixture','background failure')
            deadline=time.monotonic()+2
            while time.monotonic()<deadline:
                if journal.available and journal.summary()['unseen']==1:break
                threading.Event().wait(.01)
            self.assertEqual(1,journal.summary()['unseen'])
        finally:journal.close()
        self.assertFalse(journal.worker.is_alive())

    def test_storage_failure_retry_backoff_is_not_defeated_by_queued_messages(self):
        journal=Journal(Path(self.temp.name)/'blocked.sqlite3',start=False)
        journal.record('INFO','fixture','queued')
        def stop_after_wait(seconds):
            self.assertEqual(5,seconds)
            journal._stop.set()
        with mock.patch.object(journal,'drain',side_effect=sqlite3.OperationalError('offline')), mock.patch.object(journal._stop,'wait',side_effect=stop_after_wait):
            journal._run()
        self.assertFalse(journal.available)

    def test_sqlite_limits_apply_to_every_connection_not_only_initialization(self):
        from contextlib import closing
        for _ in range(2):
            with closing(self.journal._connect()) as conn:
                self.assertEqual(8192,conn.execute('PRAGMA max_page_count').fetchone()[0])
                self.assertEqual(128,conn.execute('PRAGMA wal_autocheckpoint').fetchone()[0])
                self.assertEqual(1048576,conn.execute('PRAGMA journal_size_limit').fetchone()[0])
                self.assertEqual(2,conn.execute('PRAGMA auto_vacuum').fetchone()[0])

    def test_authentication_frame_names_are_preserved_but_auth_payload_is_omitted(self):
        self.journal.record('ERROR','fixture','Request failed',trace='RuntimeError: storage failure\nserver.py:1 in require_api_key')
        self.journal.record('ERROR','fixture','Authentication failure',trace='Cookie: SID=private-cookie\nmultiline-secret\nserver.py:2 in require_api_key')
        self.journal.drain()
        rows=self.journal.query(errors=True)['entries']
        self.assertIn('RuntimeError',rows[1]['trace'])
        self.assertIn('require_api_key',rows[1]['trace'])
        self.assertNotIn('private-cookie',json.dumps(rows))
        self.assertNotIn('multiline-secret',json.dumps(rows))
