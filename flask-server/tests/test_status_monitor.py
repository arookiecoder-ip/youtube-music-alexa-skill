import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest import mock
from status_monitor import Metrics, CachedCollector, file_info, scan_audio, system_snapshot

class MetricsTests(unittest.TestCase):
    def test_latency_errors_history_and_cardinality_are_bounded(self):
        clock, wall = [0.0], [12000.0]
        metrics = Metrics(lambda: clock[0], lambda: wall[0])
        self.assertIsNone(metrics.snapshot()['requests']['p95_ms'])
        for i in range(1100):
            start = metrics.begin_request(); clock[0] += .01
            metrics.finish_request(start, 'GET', f'/registered/{i}', 503 if i % 2 else 200)
        data = metrics.snapshot()
        self.assertEqual(1100, data['requests']['total'])
        self.assertEqual(0, data['requests']['active'])
        self.assertEqual(550, data['requests']['5xx'])
        self.assertEqual(1024, data['requests']['latency_samples'])
        self.assertAlmostEqual(10, data['requests']['p95_ms'])
        self.assertLessEqual(len(data['routes']), 129)
        self.assertEqual(60, len(data['events']))
        self.assertEqual(60, len(data['history']))
        self.assertEqual(1100, data['history'][-1]['requests'])
        wall[0] += 120 * 60
        self.assertEqual(0, sum(row['requests'] for row in metrics.snapshot()['history']))

    def test_fetch_failures_success_and_no_raw_exception_retained(self):
        metrics = Metrics()
        with metrics.fetch('android', 'AAAAAAAAAAA') as attempt:
            self.assertEqual(1, len(metrics.snapshot()['active_fetches']))
            attempt['ok'] = True
        with self.assertRaises(RuntimeError):
            with metrics.fetch('web', 'BBBBBBBBBBB'): raise RuntimeError('private-url?key=secret')
        data = metrics.snapshot()
        self.assertEqual({'started': 2, 'succeeded': 1, 'failed': 1}, data['fetches'])
        self.assertFalse(data['active_fetches'])
        self.assertNotIn('private-url', json.dumps(data))

    def test_counters_thread_safe_and_snapshot_independent(self):
        metrics = Metrics()
        def work():
            for i in range(100):
                metrics.cache('audio_lookups', i % 2 == 0)
                metrics.finish_request(metrics.begin_request(), 'HEAD', '/audio/', 206)
        threads = [threading.Thread(target=work) for _ in range(8)]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        data = metrics.snapshot()
        self.assertEqual(800, data['requests']['total'])
        self.assertEqual(400, data['caches'][0]['hits'])
        self.assertEqual(50, data['caches'][0]['hit_rate'])
        data['events'].append({'secret': True})
        self.assertFalse(metrics.snapshot()['events'])

class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)

    def test_pool_bytes_partial_pins_and_no_sensitive_names(self):
        for name, data in [('AAAAAAAAAAA.m4a', b'a'*100), ('AAAAAAAAAAA.warmup', b'1'), ('BBBBBBBBBBB.uuid.part', b'b'*50), ('secret-key.txt', b'private'), ('CCCCCCCCCCC.webm', b'c'*25)]:
            (self.directory / name).write_bytes(data)
        (self.directory/'symlink.m4a').symlink_to(self.directory/'AAAAAAAAAAA.m4a')
        data = scan_audio(self.directory, {'AAAAAAAAAAA'})
        self.assertTrue(data['complete'])
        self.assertEqual((2, 125, 50, 100, 100), tuple(data[k] for k in ['ready_files','ready_bytes','partial_bytes','warmup_bytes','pinned_bytes']))
        self.assertEqual(3, len(data['entries']))
        for value in ['private', 'secret-key', self.temp.name]: self.assertNotIn(value, json.dumps(data))
        self.assertEqual(data['files'], data['ready_files']+data['partial_files']+data['metadata_files'])

    def test_large_inventory_bounded_and_missing_unreadable_directories(self):
        for i in range(240): (self.directory/f'{i:011}.m4a').write_bytes(b'x')
        data = scan_audio(self.directory, limit=1000, time_budget=10)
        self.assertEqual(240, data['ready_files']); self.assertEqual(200, len(data['entries']))
        self.assertTrue(data['inventory_limited']); self.assertFalse(scan_audio(self.directory, limit=10)['complete'])
        self.assertFalse(scan_audio(self.directory/'missing')['available'])
        with mock.patch('status_monitor.os.scandir', side_effect=PermissionError):
            self.assertEqual('directory_unreadable', scan_audio(self.directory)['reason'])
        self.assertFalse(file_info(None)['present'])

    def test_disk_database_outage_and_probe_never_creates_database(self):
        db = self.directory/'state.db'
        with sqlite3.connect(db) as conn: conn.execute('create table t (id integer)')
        data = system_snapshot(self.directory/'missing-cache', db)
        self.assertTrue(data['database']['readable']); self.assertGreater(data['disk']['total_bytes'],0)
        self.assertNotIn(str(self.directory), json.dumps(data))
        db.write_text('corrupt'); self.assertFalse(system_snapshot(self.directory, db)['database']['readable'])
        missing = self.directory/'absent.db'
        self.assertFalse(system_snapshot(self.directory, missing)['database']['readable']); self.assertFalse(missing.exists())

    def test_single_flight_scan_reuses_snapshot_and_never_blocks_on_concurrent_scan(self):
        now = [0]; cache = CachedCollector(clock=lambda: now[0]); collect = mock.Mock(return_value={'files': 1})
        self.assertEqual({'files': 1}, cache.get(collect)); cache.get(collect); self.assertEqual(1, collect.call_count)
        now[0] = 11; cache._lock.acquire()
        try: self.assertEqual({'files': 1}, cache.get(collect))
        finally: cache._lock.release()
        cache.get(collect); self.assertEqual(2, collect.call_count)


class CacheDetailTests(unittest.TestCase):
    def test_details_are_bounded_private_filterable_and_use_stable_cursors(self):
        now=[1000000.];metrics=Metrics(wall=lambda:now[0])
        for i in range(250):metrics.cache('audio_search',i%2==0,item=('private query',i))
        first=metrics.cache_details('audio_search',limit=50)
        self.assertEqual(200,first['retained']);self.assertEqual(50,len(first['entries']))
        self.assertNotIn('private query',json.dumps(first))
        metrics.cache('audio_search',True,item=('new private query',1))
        second=metrics.cache_details('audio_search',before=first['next_before'],limit=50)
        self.assertFalse({r['id'] for r in first['entries']} & {r['id'] for r in second['entries']})
        self.assertTrue(all(r['outcome']=='miss' for r in metrics.cache_details('audio_search','miss')['entries']))
        now[0]+=86401;self.assertEqual([],metrics.cache_details('audio_search')['entries'])
        self.assertEqual(251,metrics.snapshot()['caches'][0]['hits']+metrics.snapshot()['caches'][0]['misses'])
