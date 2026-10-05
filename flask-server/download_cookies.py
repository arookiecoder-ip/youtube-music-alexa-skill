"""Download-cookie validation and atomic, persistent replacement (no account checks)."""
from contextlib import contextmanager
import http.cookiejar
import os
import shutil
import subprocess
import tempfile
import threading
import time
import warnings
from pathlib import Path

MAX_BYTES = 128 * 1024
_probe_lock = threading.Lock()
_cache = {}


class ProbeBusy(Exception):
    pass


@contextmanager
def _exclusive_probe():
    if not _probe_lock.acquire(timeout=1):
        raise ProbeBusy('Another audio cookie test is running. Try again shortly.')
    try:
        yield
    finally:
        _probe_lock.release()


def saved_path():
    base = Path(os.environ.get('DB_FILE', str(Path(__file__).with_name('data.db')))).parent
    return Path(os.environ.get('YTDLP_COOKIE_STORE', str(base / 'download_cookies.txt')))


def _parse(path):
    try:
        jar = http.cookiejar.MozillaCookieJar(str(path))
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            jar.load(ignore_discard=True, ignore_expires=True)
    except (OSError, http.cookiejar.LoadError, ValueError):
        raise ValueError('Upload a Netscape-format cookies.txt export.') from None
    if not any(c.domain.lstrip('.') in ('youtube.com', 'music.youtube.com', 'www.youtube.com')
               and not c.is_expired() for c in jar):
        raise ValueError('The export contains no unexpired YouTube cookies.')


def _probe(path, configure_command):
    """Fetch an uncached audio sample using this export, never the live cookie jar."""
    try:
        _parse(path)
    except ValueError as exc:
        return {'valid': False, 'message': str(exc)}
    deadline = time.monotonic() + 50
    video_id = os.environ.get('YTDLP_COOKIE_TEST_VIDEO_ID', 'Yq4tcnH4bxg')
    with tempfile.TemporaryDirectory(prefix='ytm-cookie-probe-') as folder:
        for client in ('default', 'web', 'tv'):
            if time.monotonic() >= deadline:
                break
            candidate = Path(folder) / 'cookies.txt'
            shutil.copyfile(path, candidate)
            candidate.chmod(0o600)
            output = Path(folder) / 'sample.%(ext)s'
            command = ['yt-dlp', '--ignore-config', '--no-cache-dir', '--no-update',
                       '--cookies', str(candidate), '--test', '--no-playlist', '--no-progress',
                       '-f', 'bestaudio', '--socket-timeout', '8', '--retries', '0',
                       '--extractor-retries', '0', '--remote-components', 'ejs:github']
            configure_command(command)
            if client != 'default':
                command += ['--extractor-args', f'youtube:player_client={client}']
            command += ['-o', str(output), '--', video_id]
            try:
                result = subprocess.run(command, capture_output=True, timeout=max(1, deadline-time.monotonic()))
            except subprocess.TimeoutExpired:
                return {'valid': False, 'message': 'Audio download test timed out. Existing cookies were kept.'}
            except OSError:
                return {'valid': False, 'message': 'Audio downloader is unavailable. Existing cookies were kept.'}
            audio = [p for p in Path(folder).glob('sample.*')
                     if p.suffix in ('.m4a', '.webm', '.opus', '.mp3', '.ogg', '.aac') and p.stat().st_size > 1024]
            if result.returncode == 0 and audio:
                return {'valid': True, 'message': 'Audio sample downloaded successfully.',
                        'tested_at': int(time.time())}
            for sample in Path(folder).glob('sample.*'):
                sample.unlink()
    return {'valid': False, 'message': 'Audio download failed. YouTube may have rejected or blocked these cookies. Existing cookies were kept.'}


def status(path, configure_command, force=False):
    if not path or not Path(path).is_file():
        return {'valid': False, 'message': 'No download cookies saved.'}
    with _exclusive_probe():
        try:
            stat = Path(path).stat()
        except OSError:
            return {'valid': False, 'message': 'Could not read download cookies.'}
        key = (str(path), stat.st_mtime_ns, stat.st_size, stat.st_ino)
        cached = _cache.get(key)
        if not force and cached and time.monotonic() - cached[0] < 60:
            return dict(cached[1])
        try:
            result = _probe(path, configure_command)
        except OSError:
            result = {'valid': False, 'message': 'Could not read download cookies.'}
        _cache.clear()
        _cache[key] = (time.monotonic(), result)
        return dict(result)


def replace(raw, configure_command):
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError('Paste cookies or choose a cookies.txt file.')
    if len(raw.encode('utf-8')) > MAX_BYTES:
        raise ValueError('Cookie export must be smaller than 128 KB.')
    destination = saved_path()
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Same filesystem ensures promotion is atomic. Failed candidates never replace the live jar.
    with _exclusive_probe():
        fd, name = tempfile.mkstemp(prefix='.download-cookies-', dir=destination.parent)
        candidate = Path(name)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                stream.write(raw.strip() + '\n')
                stream.flush()
                os.fsync(stream.fileno())
            result = _probe(candidate, configure_command)
            if not result['valid']:
                return result
            os.replace(candidate, destination)
            _cache.clear()
            stat = destination.stat()
            _cache[(str(destination), stat.st_mtime_ns, stat.st_size, stat.st_ino)] = (time.monotonic(), result)
            return dict(result)
        finally:
            candidate.unlink(missing_ok=True)
