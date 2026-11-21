"""Operator-created, expiring migration instructions; no public write endpoint."""
import json
import os
import stat
import time
from pathlib import Path
from urllib.parse import urlsplit


def instruction(path, *, account_id, skill_id, backend_url, now=None):
    now = time.time() if now is None else now
    try:
        p = Path(path)
        info = p.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_size > 4096:
            return None
        value = json.loads(p.read_text())
        expires = value['expires']
        if not now < expires <= now + 3600:
            return None
        if value['account_id'] != account_id or value['skill_id'] != skill_id:
            return None
        target, origin = urlsplit(value['url']), urlsplit(backend_url)
        if (target.scheme != 'https' or target.netloc != origin.netloc
                or target.username or target.password or target.query or target.fragment
                or target.path != '/alexa/skill/migration/'):
            return None
        if not isinstance(value['token'], str) or len(value['token']) < 43:
            return None
        if not isinstance(value['transfer_id'], str) or not 1 <= len(value['transfer_id']) <= 128:
            return None
        return value
    except (OSError, ValueError, TypeError, KeyError):
        return None
