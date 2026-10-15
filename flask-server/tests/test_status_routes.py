import json
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest import mock
from test_next_track import server
from status_monitor import Metrics, CachedCollector

class StatusRoutesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name); self.db = self.directory/'state.db'
        with sqlite3.connect(self.db) as conn: conn.execute('create table t (id integer)')
        for name,value in [('AUDIO_CACHE_DIR', str(self.directory)), ('DB_FILE', str(self.db)), ('_operational',Metrics()), ('_status_storage', CachedCollector())]:
            patcher=mock.patch.object(server,name,value); patcher.start(); self.addCleanup(patcher.stop)
        self.client=server.app.test_client(); self.headers={'X-Api-Key':server.API_KEY}

    def test_owner_only_no_store_and_key_page_without_credentials(self):
        with mock.patch.object(server, '_ensure_db'):
            self.assertEqual(401,self.client.get('/api/status').status_code)
            self.assertEqual(302,self.client.get('/status').status_code)
            with mock.patch.object(server,'_jam_guest',return_value=True):
                self.assertEqual(401,self.client.get('/api/status').status_code)
                self.assertEqual(302,self.client.get('/status').status_code)
        response=self.client.get('/api/status',headers=self.headers)
        self.assertEqual(200,response.status_code); self.assertIn('no-store',response.headers['Cache-Control'])
        self.assertEqual('current_process',response.json['scope'])
        page=self.client.get('/status/?key='+server.API_KEY)
        self.assertEqual(200,page.status_code); self.assertIn('script-src',page.headers['Content-Security-Policy'])
        self.assertEqual('no-referrer',page.headers['Referrer-Policy'])
        self.assertNotIn(server.API_KEY,page.get_data(as_text=True))
        with mock.patch.object(server,'_ensure_db'), mock.patch.object(server,'_logged_in',return_value=True):
            self.assertEqual(200,self.client.get('/api/status/').status_code)
            self.assertEqual(200,self.client.get('/status').status_code)

    def test_passive_snapshot_no_external_probe_and_no_secret_values(self):
        cookie=self.directory/'cookie.txt'; cookie.write_text('private-cookie')
        with mock.patch.object(server.Supporting,'get_ytdlp_cookies_file',return_value=str(cookie)), mock.patch.object(server.download_cookies,'_probe',side_effect=AssertionError('network')), mock.patch.object(server,'_ytmusic_is_authenticated',side_effect=AssertionError('network')):
            response=self.client.get('/api/status',headers=self.headers)
        self.assertTrue(response.json['cookies']['downloads']['present'])
        for value in ['private-cookie',str(cookie),server.API_KEY,'test-owner']: self.assertNotIn(value,response.get_data(as_text=True))
        self.assertIsNone(response.json['cookies']['downloads']['validation'])

    def test_real_request_and_cache_metrics_exclude_dashboard_and_queries(self):
        with mock.patch.object(server,'_ensure_db'):
            self.client.get('/not-real?key=top-secret&cookie=private')
            self.client.get('/api/track/not-real?key=wrong')
        (self.directory/'AAAAAAAAAAA.m4a').write_bytes(b'a')
        server.Supporting.cached_audio_path('AAAAAAAAAAA'); server.Supporting.cached_audio_path('BBBBBBBBBBB')
        data=self.client.get('/api/status',headers=self.headers).json
        self.assertEqual(2,data['requests']['total']); self.assertEqual(0,data['requests']['active'])
        self.assertEqual(2,sum(row['errors'] for row in data['routes']))
        cache=next(row for row in data['caches'] if row['name']=='audio_lookups')
        self.assertEqual((1,1),(cache['hits'],cache['misses']))
        self.assertNotIn('top-secret',json.dumps(data)); self.assertNotIn('not-real',json.dumps(data['routes']))
        self.assertEqual(2,self.client.get('/api/status',headers=self.headers).json['requests']['total'])

    def test_critical_disk_pressure_and_database_outage(self):
        with mock.patch.object(server,'AUDIO_CACHE_MIN_FREE_BYTES',10**30):
            self.assertEqual('critical',self.client.get('/api/status',headers=self.headers).json['health'])
        self.db.write_text('corrupt'); server._status_storage=CachedCollector()
        with mock.patch.object(server,'_ensure_db',side_effect=RuntimeError('unavailable')):
            response=self.client.get('/api/status',headers=self.headers)
        self.assertEqual(200,response.status_code); self.assertFalse(response.json['storage']['system']['database']['readable'])
        self.assertEqual('critical',response.json['health'])

    def test_cookie_replacement_invalidates_validation_and_busy_probe_nonblocking(self):
        cookie=self.directory/'cookies'; cookie.write_text('value'); stat=cookie.stat(); store=server.download_cookies
        key=(str(cookie),stat.st_mtime_ns,stat.st_size,stat.st_ino)
        with mock.patch.dict(store._cache,{key:(time.monotonic(),{'valid':True})},clear=True):
            self.assertTrue(store.inspection(str(cookie))['validation']); cookie.write_text('different')
            self.assertIsNone(store.inspection(str(cookie))['validation'])
        store._probe_lock.acquire()
        try: self.assertTrue(store.inspection(str(cookie))['probe_busy'])
        finally: store._probe_lock.release()

    def test_local_collector_failure_preserves_other_metrics_without_raw_errors(self):
        with mock.patch.object(server.alexa_remote.remote,'is_logged_in',side_effect=RuntimeError('secret-upstream-url')):
            response=self.client.get('/api/status',headers=self.headers)
        self.assertEqual(200,response.status_code)
        self.assertIn('Amazon session unavailable',[row['title'] for row in response.json['alerts']])
        self.assertNotIn('secret-upstream-url',response.get_data(as_text=True))

    def test_passive_cookie_expiry_counts_and_oversized_export(self):
        cookie = self.directory / 'cookies.txt'
        future = int(time.time()) + 3600
        cookie.write_text('# Netscape HTTP Cookie File\n'
            f'.youtube.com\tTRUE\t/\tTRUE\t{future}\tactive\tprivate-active\n'
            '.youtube.com\tTRUE\t/\tTRUE\t1\texpired\tprivate-expired\n'
            '.youtube.com\tTRUE\t/\tTRUE\t\tsession\tprivate-session\n')
        result = server.download_cookies.inspection(cookie)
        self.assertTrue(result['format_valid'])
        self.assertEqual((3, 1, 1, 2), tuple(result[key] for key in
            ('cookie_count', 'expired_count', 'session_count', 'youtube_active')))
        self.assertEqual(future, result['next_expiry'])
        self.assertNotIn('private-', json.dumps(result))
        cookie.write_bytes(b'x' * (server.download_cookies.MAX_BYTES + 1))
        self.assertIsNone(server.download_cookies.inspection(cookie)['format_valid'])
