import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import download_cookies as cookies

EXPORT = '# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t2000000000\tSID\ttest-value\n'


class DownloadCookies(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.saved = Path(self.temp.name) / 'saved.txt'
        self.original = Path(self.temp.name) / 'original.txt'
        self.original.write_text(EXPORT)
        self.env = mock.patch.dict(os.environ, {'YTDLP_COOKIE_STORE': str(self.saved)})
        self.env.start()
        self.addCleanup(self.env.stop)
        cookies._cache.clear()

    def _download(self, command, **kwargs):
        output = command[command.index('-o') + 1].replace('%(ext)s', 'm4a')
        Path(output).write_bytes(b'audio' * 1024)
        return subprocess.CompletedProcess(command, 0)

    def test_status_requires_downloaded_bytes_not_just_exit_zero(self):
        with mock.patch.object(cookies.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)):
            self.assertFalse(cookies.status(self.original, lambda _: None)['valid'])

    def test_probe_is_uncached_and_uses_private_copy_and_download(self):
        commands = []
        def download(command, **kwargs):
            commands.append(command)
            probe = Path(command[command.index('--cookies') + 1])
            self.assertNotEqual(probe, self.original)
            self.assertEqual(probe.stat().st_mode & 0o777, 0o600)
            probe.write_text('rotated by downloader')
            return self._download(command, **kwargs)
        with mock.patch.object(cookies.subprocess, 'run', side_effect=download):
            self.assertTrue(cookies.status(self.original, lambda _: None)['valid'])
        self.assertIn('--test', commands[0])
        self.assertIn('--no-cache-dir', commands[0])
        self.assertNotIn('--flat-playlist', commands[0])
        self.assertEqual(self.original.read_text(), EXPORT)
        self.assertFalse(Path(commands[0][commands[0].index('--cookies') + 1]).exists())

    def test_failed_replacement_keeps_old_cookie_and_cleans_candidate(self):
        self.saved.write_text('old cookie')
        with mock.patch.object(cookies.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1)):
            self.assertFalse(cookies.replace(EXPORT, lambda _: None)['valid'])
        self.assertEqual(self.saved.read_text(), 'old cookie')
        self.assertEqual(list(self.saved.parent.glob('.download-cookies-*')), [])

    def test_successful_save_is_private_atomic_and_preserves_original_mount(self):
        with mock.patch.object(cookies.subprocess, 'run', side_effect=self._download):
            self.assertTrue(cookies.replace(EXPORT, lambda _: None)['valid'])
        self.assertEqual(self.saved.read_text(), EXPORT)
        self.assertEqual(self.saved.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.original.read_text(), EXPORT)
        self.assertEqual(list(self.saved.parent.glob('.download-cookies-*')), [])

    def test_rejects_malformed_expired_empty_and_oversized_exports_without_network(self):
        for raw in ('', 'not cookies', EXPORT.replace('2000000000', '1'), 'x' * (cookies.MAX_BYTES + 1)):
            with mock.patch.object(cookies.subprocess, 'run') as run:
                try:
                    result = cookies.replace(raw, lambda _: None)
                except ValueError:
                    result = {'valid': False}
                self.assertFalse(result['valid'])
                run.assert_not_called()
                self.assertFalse(self.saved.exists())

    def test_timeout_and_missing_downloader_fail_without_saving(self):
        for failure in (subprocess.TimeoutExpired([], 50), FileNotFoundError()):
            with mock.patch.object(cookies.subprocess, 'run', side_effect=failure):
                self.assertFalse(cookies.replace(EXPORT, lambda _: None)['valid'])
                self.assertFalse(self.saved.exists())

    def test_concurrent_upload_is_rejected_without_changing_saved_cookie(self):
        self.saved.write_text('old cookie')
        cookies._probe_lock.acquire()
        try:
            with self.assertRaises(cookies.ProbeBusy):
                cookies.replace(EXPORT, lambda _: None)
        finally:
            cookies._probe_lock.release()
        self.assertEqual(self.saved.read_text(), 'old cookie')

    def test_force_status_bypasses_cache(self):
        with mock.patch.object(cookies.subprocess, 'run', side_effect=self._download) as run:
            self.assertTrue(cookies.status(self.original, lambda _: None)['valid'])
            self.assertTrue(cookies.status(self.original, lambda _: None)['valid'])
            self.assertEqual(run.call_count, 1)
            self.assertTrue(cookies.status(self.original, lambda _: None, force=True)['valid'])
            self.assertEqual(run.call_count, 2)


if __name__ == '__main__':
    unittest.main()
