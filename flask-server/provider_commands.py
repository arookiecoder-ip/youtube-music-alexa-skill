"""Operation-scoped legacy provider credentials and child deadlines."""
import atexit
import os
import shutil
import subprocess
import tempfile
import threading

_files = set()
_lock = threading.Lock()


def attach_cookies(command, source):
    fd, path = tempfile.mkstemp(prefix='musicbox-cookies-', suffix='.txt')
    try:
        with os.fdopen(fd, 'wb') as target, open(source, 'rb') as original:
            shutil.copyfileobj(original, target)
        with _lock:
            _files.add(path)
        command.extend(['--cookies', path])
    except BaseException:
        os.unlink(path)
        raise


def cleanup(command):
    with _lock:
        paths = set(command) & _files
        _files.difference_update(paths)
    for path in paths:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass


def run(command, **kwargs):
    try:
        kwargs.setdefault('timeout', 25)
        return subprocess.run(command, **kwargs)
    finally:
        cleanup(command)


def _close():
    with _lock:
        paths = list(_files)
    cleanup(paths)


atexit.register(_close)
