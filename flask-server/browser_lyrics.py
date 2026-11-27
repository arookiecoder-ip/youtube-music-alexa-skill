"""Bounded, identity-scoped lyrics lookup for the browser player."""
from collections import OrderedDict
import threading
import time


def normalize_lyrics(result):
    result = result if isinstance(result, dict) else {}
    raw = result.get('lyrics')
    lines = []
    if isinstance(raw, list):
        remaining = 64000
        for line in raw[:2000]:
            text = line.get('text', '') if isinstance(line, dict) else getattr(line, 'text', '')
            start = line.get('start_time', 0) if isinstance(line, dict) else getattr(line, 'start_time', 0)
            if not isinstance(text, str) or not isinstance(start, (int, float)) or start < 0:
                continue
            text = text[:remaining]
            if not text:
                continue
            remaining -= len(text)
            lines.append({'text': text, 'start_ms': int(start)})
            if remaining <= 0:
                break
        lines.sort(key=lambda line: line['start_ms'])
        text = '\n'.join(line['text'] for line in lines)
    else:
        text = raw[:64000] if isinstance(raw, str) else ''
    return {'available': bool(text), 'text': text, 'lines': lines,
            'source': str(result.get('source') or '')[:256]}


class BrowserLyrics:
    def __init__(self, limit=128, now=time.monotonic):
        self.limit, self.now = limit, now
        self.entries = OrderedDict()
        self.lock = threading.Lock()

    def get(self, identity, video, provider, refresh=False):
        key = (identity, video)
        with self.lock:
            cached = self.entries.pop(key, None)
            if cached and cached[0] > self.now() and not refresh:
                self.entries[key] = cached
                return cached[1]
        playlist = provider.get_watch_playlist(videoId=video, limit=1) or {}
        browse = playlist.get('lyrics')
        result = None
        if browse:
            try:
                result = provider.get_lyrics(browse, timestamps=True)
            except Exception:
                result = None
            if not result or not result.get('lyrics'):
                result = provider.get_lyrics(browse)
        normalized = normalize_lyrics(result)
        with self.lock:
            self.entries[key] = (self.now() + (3600 if normalized['available'] else 60), normalized)
            while len(self.entries) > self.limit:
                self.entries.popitem(last=False)
        return normalized
