import json
import asyncio
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest import mock
from test_next_track import server
from status_monitor import Metrics, CachedCollector
from status_journal import Journal

class StatusRoutesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name); self.db = self.directory/'state.db'
        with sqlite3.connect(self.db) as conn: conn.execute('create table t (id integer)')
        for name,value in [('AUDIO_CACHE_DIR', str(self.directory)), ('DB_FILE', str(self.db)), ('_operational',Metrics()), ('_status_storage', CachedCollector())]:
            patcher=mock.patch.object(server,name,value); patcher.start(); self.addCleanup(patcher.stop)
        self.journal=Journal(self.directory/'logs.sqlite3', secrets=lambda:(server.API_KEY,), start=False); self.journal.drain()
        patcher=mock.patch.object(server,'_status_journal',self.journal);patcher.start();self.addCleanup(patcher.stop)
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

    def test_snapshot_describes_shared_capacity_retention_and_partial_timeout(self):
        data = self.client.get('/api/status', headers=self.headers).json
        self.assertEqual('capacity_lru', data['limits']['retention_policy'])
        self.assertIsNone(data['limits']['audio_ttl_seconds'])
        self.assertIsNone(data['limits']['warmup_ttl_seconds'])
        self.assertEqual(0, data['limits']['warmup_bytes'])
        self.assertEqual(server.AUDIO_CACHE_TTL, data['limits']['partial_ttl_seconds'])

    def test_over_budget_cache_has_explicit_warning(self):
        with mock.patch.object(server, 'AUDIO_CACHE_MAX_BYTES', 10), mock.patch.object(server._status_storage, 'get', return_value={'audio': {'ready_bytes': 11}, 'system': {}}):
            data = self.client.get('/api/status', headers=self.headers).json
        self.assertIn('Music cache is over budget', [row['title'] for row in data['alerts']])
        self.assertEqual('warning', data['health'])

    def test_log_cache_and_error_endpoints_are_owner_only_and_no_store(self):
        self.journal.record('ERROR','fixture','failure');self.journal.drain()
        ident=self.journal.query(errors=True)['entries'][0]['id']
        for path in ['/api/status/logs','/api/status/errors','/api/status/errors/'+ident,'/api/status/cache/audio_lookups']:
            self.assertEqual(401,self.client.get(path).status_code)
            with mock.patch.object(server,'_jam_guest',return_value=True):self.assertEqual(401,self.client.get(path).status_code)
            response=self.client.get(path,headers=self.headers)
            self.assertEqual(200,response.status_code);self.assertIn('no-store',response.headers['Cache-Control'])
        self.assertEqual(401,self.client.post('/api/status/errors/seen',json={'ids':[ident]}).status_code)
        self.assertEqual(1,self.journal.summary()['unseen'])
        response=self.client.post('/api/status/errors/seen',json={'ids':[ident]},headers=self.headers)
        self.assertEqual(0,response.json['unseen'])

    def test_bad_filters_cursors_and_seen_payloads_are_rejected(self):
        for query in ['limit=0','limit=101','before=-1','before=99999999999999999999999','level=unknown','q='+('x'*201)]:
            self.assertEqual(400,self.client.get('/api/status/logs?'+query,headers=self.headers).status_code)
        self.assertEqual(404,self.client.get('/api/status/cache/unknown',headers=self.headers).status_code)
        self.assertEqual(400,self.client.get('/api/status/cache/audio_lookups?outcome=invalid',headers=self.headers).status_code)
        for payload in [{}, {'ids':[]}, {'ids':['bad']}, {'ids':['a'*32]*101}]:
            self.assertEqual(400,self.client.post('/api/status/errors/seen',headers=self.headers,json=payload).status_code)
        self.assertEqual(404,self.client.get('/api/status/errors/'+'a'*32,headers=self.headers).status_code)
        self.assertEqual(404,self.client.get('/api/status/errors/not-an-id',headers=self.headers).status_code)

    def test_http_errors_and_logger_errors_capture_context_without_status_poll_spam(self):
        with mock.patch.object(server,'_ensure_db'):
            self.client.get('/not-a-route?key='+server.API_KEY,headers=self.headers)
        server.logger.error('Failed audio decoding')
        self.client.get('/api/status',headers=self.headers)
        self.client.get('/api/status/logs',headers=self.headers)
        self.journal.drain()
        rows=self.journal.query()['entries']
        self.assertTrue(any(row['source']=='http' for row in rows))
        self.assertTrue(any(row['message']=='Failed audio decoding' for row in rows))
        self.assertFalse(any('/api/status' in row['route'] for row in rows))
        self.assertNotIn(server.API_KEY,json.dumps(rows))

    def test_log_storage_outage_does_not_break_status_and_recovery_is_visible(self):
        self.journal.available=False
        self.assertEqual(503,self.client.get('/api/status/logs',headers=self.headers).status_code)
        self.assertEqual(200,self.client.get('/api/status',headers=self.headers).status_code)
        self.journal.drain()
        self.assertEqual(200,self.client.get('/api/status/logs',headers=self.headers).status_code)

    def test_expired_error_is_not_acknowledged_or_returned(self):
        self.journal.record('ERROR','fixture','old failure');self.journal.drain()
        ident=self.journal.query(errors=True)['entries'][0]['id']
        with mock.patch.object(self.journal,'clock',return_value=time.time()+86401):
            self.assertEqual(404,self.client.get('/api/status/errors/'+ident,headers=self.headers).status_code)
            self.assertEqual(0,self.client.post('/api/status/errors/seen',headers=self.headers,json={'ids':[ident]}).json['unseen'])

    def test_cache_details_do_not_leak_raw_keys_and_expiry_is_a_miss(self):
        cache=server._BoundedCache(name='audio_search');cache.set(('private search query',123),(time.time()-200,'value'))
        self.assertIsNone(cache.get(('private search query',123),max_age=100))
        response=self.client.get('/api/status/cache/audio_search?outcome=miss',headers=self.headers)
        self.assertEqual(1,len(response.json['entries']))
        self.assertEqual('expired',response.json['entries'][0]['reason'])
        self.assertNotIn('private search query',response.get_data(as_text=True))
        self.assertNotIn('entries',self.client.get('/api/status',headers=self.headers).json['caches'][0])

    def test_watchdog_reports_health_and_stuck_fetches_without_spam(self):
        snapshot={'alerts':[{'level':'critical','title':'Disk low','detail':'Below reserve'}],
                  'active_fetches':[{'started_at':time.time()-130,'video_id':'AAAAAAAAAAA'}]}
        with mock.patch.object(server,'_status_snapshot',return_value=snapshot), mock.patch.object(server,'_status_watchdog_keys',set()):
            server._status_watchdog_once();server._status_watchdog_once();self.journal.drain()
            self.assertEqual(2,self.journal.summary()['unseen'])
            snapshot['alerts']=[];snapshot['active_fetches']=[];server._status_watchdog_once()
            snapshot['alerts']=[{'level':'critical','title':'Disk low','detail':'Below reserve'}]
            server._status_watchdog_once();self.journal.drain();self.assertEqual(3,self.journal.summary()['unseen'])

    def test_normal_search_activity_is_reported_separately_from_cache_lookups(self):
        server._operational.finish_request(server._operational.begin_request(),'GET','/alexa/search/',200)
        data=self.client.get('/api/status',headers=self.headers).json
        self.assertEqual(1,data['search_activity']['requests'])
        self.assertEqual([],data['caches'])

    def test_background_thread_exception_is_captured_without_hiding_original_hook(self):
        import types
        try:raise RuntimeError('audio worker failed')
        except RuntimeError as error:
            args=types.SimpleNamespace(exc_type=type(error),exc_value=error,exc_traceback=error.__traceback__)
            with mock.patch.object(server,'_status_original_thread_hook') as original:
                server._status_thread_exception(args);original.assert_called_once_with(args)
        self.journal.drain()
        self.assertTrue(any(row['source']=='thread' for row in self.journal.query(errors=True)['entries']))

    def test_cookie_session_acknowledgement_requires_json(self):
        with mock.patch.object(server,'_logged_in',return_value=True):
            response=self.client.post('/api/status/errors/seen',data={'ids':'a'*32})
        self.assertEqual(401,response.status_code)

    def test_unhandled_http_500_is_recorded_with_correlated_exception(self):
        with mock.patch.object(server,'_ensure_db',side_effect=RuntimeError('storage failure')), mock.patch.dict(server.app.config,{'PROPAGATE_EXCEPTIONS':False}):
            response=self.client.get('/api/app/devices/',headers=self.headers)
        self.assertEqual(500,response.status_code)
        self.journal.drain()
        errors=self.journal.query(errors=True)['entries']
        http=next(row for row in errors if row['source']=='http')
        self.assertEqual(32,len(http['request_id']))
        self.assertTrue(any(row['request_id']==http['request_id'] and 'RuntimeError' in row['trace'] for row in errors))

    def test_non_ascii_invalid_api_key_is_rejected_without_server_error(self):
        for path in ['/api/status','/api/status/logs','/api/status/errors','/api/status/cache/audio_lookups']:
            self.assertEqual(401,self.client.get(path,headers={'X-Api-Key':'invalid-☃'}).status_code)

    def test_real_owner_login_without_api_key_can_read_details_and_mark_seen(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS web_sessions (sid TEXT PRIMARY KEY, created_at REAL)')
        with mock.patch.object(server,'_valid_sids',set()), mock.patch.object(server,'_totp_enabled',return_value=False):
            login=self.client.post('/login/',json={'username':server.REMOTE_USER,'password':server.REMOTE_PASSWORD})
            self.assertEqual(200,login.status_code);self.assertTrue(login.json['ok'])
            self.journal.record('ERROR','fixture','session test failure');self.journal.drain()
            ident=self.journal.query(errors=True)['entries'][0]['id']
            for path in ['/status','/api/status','/api/status/cache/account_ytmusic_clients','/api/status/logs','/api/status/errors','/api/status/errors/'+ident]:
                response=self.client.get(path)
                self.assertEqual(200,response.status_code,path)
                self.assertIn('no-store',response.headers['Cache-Control'])
            self.assertEqual(401,self.client.post('/api/status/errors/seen',data={'ids':ident}).status_code)
            response=self.client.post('/api/status/errors/seen',json={'ids':[ident]})
            self.assertEqual(200,response.status_code);self.assertEqual(0,response.json['unseen'])
            self.assertFalse(server._is_web_session_path('/api/status-not-real'))
            self.assertFalse(server._is_web_session_path('/api/statusX/cache'))
            with self.client.session_transaction() as session:sid=session['sid']
            server._session_close(sid)
            for path in ['/api/status/cache/account_ytmusic_clients','/api/status/logs','/api/status/errors']:
                self.assertEqual(401,self.client.get(path).status_code,path)


class StatusCacheIntegrationTests(unittest.TestCase):
    setUp = StatusRoutesTests.setUp
    def test_specialized_lookup_paths_populate_clickable_details(self):
        yt = mock.Mock()
        yt.search.return_value = [{'videoId': 'AAAAAAAAAAA', 'title': 'Fixture', 'artists': [{'name': 'Artist'}], 'duration_seconds': 120}]
        yt.get_playlist.return_value = {'tracks': yt.search.return_value}
        with mock.patch.object(server, '_audio_search_cache', server._BoundedCache(maxsize=512, name='audio_search')), \
             mock.patch.object(server, '_stream_list_cache', {}), \
             mock.patch.object(server, '_stream_list_pending', {}), \
             mock.patch.object(server, '_playlist_tracks_cache', {}), \
             mock.patch.object(server, '_playlist_tracks_pending', {}), \
             mock.patch.object(server, '_get_ytmusic', return_value=yt), \
             mock.patch.object(server, '_get_ytmusic_home', return_value=yt), \
             mock.patch.object(server.Supporting, 'find_stream_list', new=mock.AsyncMock(return_value={'queue': ['AAAAAAAAAAA']})):
            for _ in range(2):
                self.assertEqual('AAAAAAAAAAA', server._audio_search_song('private fixture query')['video_id'])
                self.assertIsNotNone(server._prepare_stream_list_cache('private fixture query'))
                self.assertIsNotNone(asyncio.run(server.Supporting.get_playlist_tracks('PLfixture')))
            for name in ('audio_search', 'stream_search', 'playlist_tracks'):
                response = self.client.get('/api/status/cache/'+name, headers=self.headers)
                self.assertEqual(200, response.status_code)
                self.assertEqual({'hits': 1, 'misses': 1}, response.json['totals'])
                self.assertEqual(2, len(response.json['entries']))
                self.assertTrue(response.json['usage']['description'])
                self.assertNotIn('private fixture query', response.get_data(as_text=True))

    def test_related_playlist_browse_requests_are_not_reported_as_cache_hits(self):
        metrics = server._operational
        metrics.finish_request(metrics.begin_request(), 'GET', '/api/library/playlists/<pl_id>', 200)
        data = self.client.get('/api/status/cache/playlist_tracks', headers=self.headers).json
        self.assertEqual({}, data['totals'])
        self.assertEqual(1, data['usage']['activity'][0]['requests'])
        self.assertIn('bypass', data['usage']['description'])
        snapshot = self.client.get('/api/status', headers=self.headers).json
        self.assertNotIn('activity', snapshot['cache_scopes']['playlist_tracks'])
