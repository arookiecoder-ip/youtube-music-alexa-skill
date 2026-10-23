"""Persistent YouTube resolver and bounded, disk-free audio relay.

Signed URLs stay on a private child-process pipe. The public endpoints retain
Flask authorization and never redirect clients to a credential-bearing URL.
"""
import atexit
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import threading
import urllib.error
import urllib.request
from urllib.parse import urlsplit


class StreamError(Exception):
    def __init__(self, code='unavailable', status=503, headers=None):
        super().__init__(code)
        self.code, self.status = code, status
        self.headers = headers or {}


def valid_media_url(url):
    try:
        parsed = urlsplit(url)
        return (parsed.scheme == 'https' and
                (parsed.hostname or '').endswith('.googlevideo.com') and
                parsed.port in (None, 443) and not parsed.username and not parsed.password)
    except (TypeError, ValueError):
        return False


class _MediaRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not valid_media_url(newurl):
            raise StreamError('invalid_media', 502)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class Relay:
    def __init__(self, upstream, first, release):
        self.upstream, self.first, self.release = upstream, first, release
        self.status = upstream.status
        self.headers = {k: upstream.headers[k] for k in (
            'Content-Type', 'Content-Length', 'Content-Range', 'Accept-Ranges')
            if upstream.headers.get(k)}
        self._lock = threading.Lock()
        self.closed = False

    def close(self):
        with self._lock:
            if self.closed:
                return
            self.closed = True
        try:
            self.upstream.close()
        finally:
            self.release()

    def chunks(self):
        try:
            if self.first:
                yield self.first
            while True:
                block = self.upstream.read1(16384)
                if not block:
                    break
                yield block
        finally:
            self.close()


class YouTubeStream:
    def __init__(self, cookie_file, enabled=False, worker=None, timeout=10,
                 opener=None, popen=subprocess.Popen, capacity=8):
        self.enabled, self.cookie_file = enabled, cookie_file
        self.worker = worker or str(Path(__file__).with_name('youtube-stream') / 'worker.mjs')
        self.timeout, self.popen = timeout, popen
        self.opener = opener or urllib.request.build_opener(
            urllib.request.ProxyHandler({}), _MediaRedirect())
        self.slots = threading.BoundedSemaphore(capacity)
        self._lock = threading.RLock()
        self._process = None
        self._cookie_path = None
        self._pending = {}
        self._sequence = 0
        atexit.register(self.close)

    def close(self):
        with self._lock:
            process, self._process = self._process, None
            if process:
                try:
                    process.terminate()
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)
                except OSError:
                    pass
                if process.stdin:
                    try:
                        process.stdin.close()
                    except OSError:
                        pass
            for waiter in self._pending.values():
                try:
                    waiter.put_nowait({'error': 'worker_stopped'})
                except queue.Full:
                    pass
            self._pending.clear()

    def _reader(self, process):
        try:
            for line in process.stdout:
                try:
                    reply = json.loads(line)
                except (ValueError, TypeError):
                    continue
                with self._lock:
                    if process is not self._process:
                        return
                    waiter = self._pending.get(reply.get('id'))
                    if waiter:
                        try:
                            waiter.put_nowait(reply)
                        except queue.Full:
                            pass
        finally:
            with self._lock:
                if process is self._process:
                    self.close()
            process.stdout.close()

    def resolve(self, video_id=None, refresh=False):
        if video_id is not None and not re.fullmatch(r'[A-Za-z0-9_-]{11}', video_id):
            raise StreamError('invalid_video', 400)
        waiter = queue.Queue(maxsize=1)
        with self._lock:
            cookie_path = self.cookie_file() or ''
            if self._process and cookie_path != self._cookie_path:
                self.close()
            if len(self._pending) >= 8:
                raise StreamError('busy')
            if not self._process or self._process.poll() is not None:
                self.close()
                # The worker only needs runtime/TLS settings, not Amazon
                # credentials, Flask secrets or the application's API key.
                env = {k: os.environ[k] for k in (
                    'PATH', 'HOME', 'SSL_CERT_FILE', 'SSL_CERT_DIR', 'NODE_EXTRA_CA_CERTS')
                    if k in os.environ}
                try:
                    allowed = [str(Path(self.worker).resolve().parent)]
                    if cookie_path:
                        allowed.append(str(Path(cookie_path).resolve()))
                    if env.get('NODE_EXTRA_CA_CERTS'):
                        allowed.append(env['NODE_EXTRA_CA_CERTS'])
                    self._process = self.popen(
                        [os.environ.get('YOUTUBE_NODE', 'node'), '--max-old-space-size=256',
                         '--experimental-permission', *['--allow-fs-read='+path for path in allowed],
                         self.worker],
                        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                        stderr=subprocess.DEVNULL, text=True, bufsize=1, env=env)
                    self._cookie_path = cookie_path
                except OSError:
                    raise StreamError('worker_unavailable') from None
                threading.Thread(target=self._reader, args=(self._process,),
                                 name='youtube-resolver', daemon=True).start()
            self._sequence += 1
            request_id = self._sequence
            self._pending[request_id] = waiter
            try:
                self._process.stdin.write(json.dumps({
                    'id': request_id, 'action': 'init' if video_id is None else 'resolve',
                    'video_id': video_id, 'cookie_file': cookie_path, 'refresh': refresh})+'\n')
                self._process.stdin.flush()
            except (OSError, ValueError):
                self.close()
                raise StreamError('worker_unavailable') from None
        try:
            try:
                reply = waiter.get(timeout=self.timeout)
            except queue.Empty:
                # A hung worker must not poison every future song.
                with self._lock:
                    self.close()
                raise StreamError('resolve_timeout') from None
            if reply.get('error'):
                code = reply['error']
                raise StreamError(code, {'track_unavailable':404, 'unsupported_format':422,
                                        'invalid_video':400}.get(code, 503))
            result = reply.get('result', {})
            if video_id is not None and not valid_media_url(result.get('url')):
                raise StreamError('invalid_media', 502)
            return result
        finally:
            with self._lock:
                self._pending.pop(request_id, None)

    def open(self, video_id, method='GET', byte_range=None, if_range=None):
        if byte_range and not re.fullmatch(r'bytes=(?:\d+-\d*|-\d+)', byte_range):
            raise StreamError('invalid_range', 416)
        if not self.slots.acquire(blocking=False):
            raise StreamError('busy')
        upstream = None
        try:
            for attempt in range(2):
                media = self.resolve(video_id, refresh=bool(attempt))
                etag = '"'+hashlib.sha256(
                    f'{video_id}:{media.get("itag")}:{media.get("length")}:{media.get("modified")}'.encode()
                ).hexdigest()+'"'
                headers = {'Accept-Encoding': 'identity'}
                # If-Range must compare the same stable representation, not a
                # signed URL that changes each time YouTube refreshes it.
                selected_range = byte_range if not if_range or if_range == etag else None
                expected_range = None
                if selected_range:
                    # Googlevideo rejects suffix ranges on some edges. Turn
                    # every supported single range into explicit byte bounds,
                    # using the fixed format's known complete length.
                    length = int(media['length'])
                    left, right = selected_range[6:].split('-', 1)
                    if left:
                        start = int(left)
                        end = min(int(right), length-1) if right else length-1
                    else:
                        suffix = int(right)
                        start, end = max(0, length-suffix), length-1
                        if suffix == 0:
                            start = length
                    if start >= length or end < start:
                        raise StreamError('invalid_range', 416, {'Content-Range':f'bytes */{length}'})
                    selected_range = f'bytes={start}-{end}'
                    expected_range = f'bytes {start}-{end}/{length}'
                    headers['Range'] = selected_range
                try:
                    upstream = self.opener.open(urllib.request.Request(
                        media['url'], headers=headers, method=method), timeout=10)
                    if upstream.status not in (200, 206):
                        raise StreamError('upstream_error', 502)
                    if selected_range and upstream.status != 206:
                        raise StreamError('range_ignored', 502)
                    if selected_range and upstream.headers.get('Content-Range') != expected_range:
                        raise StreamError('range_mismatch', 502)
                    if not upstream.headers.get('Content-Type', '').startswith('audio/mp4'):
                        raise StreamError('invalid_audio', 502)
                    first = upstream.read1(16384) if method != 'HEAD' else b''
                    if method != 'HEAD' and not first:
                        raise StreamError('empty_audio', 502)
                    relay = Relay(upstream, first, self.slots.release)
                    relay.headers.update({'ETag': etag, 'Cache-Control': 'no-store',
                                          'Accept-Ranges': 'bytes', 'X-Audio-Delivery': 'youtube-stream'})
                    return relay
                except urllib.error.HTTPError as error:
                    status = error.code
                    error.close()
                    if status in (403, 410) and attempt == 0:
                        continue
                    raise StreamError('upstream_rate_limited' if status == 429 else 'upstream_error',
                                      416 if status == 416 else 503 if status == 429 else 502) from None
            raise StreamError()
        except Exception as error:
            if upstream:
                upstream.close()
            self.slots.release()
            if isinstance(error, StreamError):
                raise
            raise StreamError('upstream_unavailable') from None
