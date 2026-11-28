"""Bounded, identity-scoped lyrics lookup for the browser player."""
from collections import OrderedDict
import threading
import time
import re
import math
import requests


_WORD_CUE = re.compile(r'<(\d{1,3}):([0-5]\d(?:\.\d{1,3})?)>')


def split_word_cues(text, line_start):
    """Read explicit Enhanced LRC word cues, preserving spaces and punctuation."""
    cues = list(_WORD_CUE.finditer(text))
    if not cues:
        return text, []
    plain = _WORD_CUE.sub('', text)
    if len(cues) > 128 or text[:cues[0].start()].strip():
        return plain, []
    words = []
    previous = line_start
    for index, cue in enumerate(cues):
        start = round((int(cue[1]) * 60 + float(cue[2])) * 1000)
        end = cues[index + 1].start() if index + 1 < len(cues) else len(text)
        if start < previous:
            return plain, []
        words.append({'text': text[cue.end():end], 'start_ms': start})
        previous = start
    words[0]['text'] = text[:cues[0].start()] + words[0]['text']
    return plain, [word for word in words if word['text']]


def normalize_lyrics(result):
    result = result if isinstance(result, dict) else {}
    raw = result.get('lyrics')
    lines = []
    if isinstance(raw, list):
        remaining = 64000
        for line in raw[:2000]:
            text = line.get('text', '') if isinstance(line, dict) else getattr(line, 'text', '')
            start = line.get('start_time', 0) if isinstance(line, dict) else getattr(line, 'start_time', 0)
            if not isinstance(text, str) or not isinstance(start, (int, float)) or not math.isfinite(start) or start < 0:
                continue
            text = text[:remaining]
            if not text:
                continue
            remaining -= len(text)
            text, words = split_word_cues(text, start)
            line = {'text': text, 'start_ms': int(start)}
            if words:
                line['words'] = words
            lines.append(line)
            if remaining <= 0:
                break
        lines.sort(key=lambda line: line['start_ms'])
        text = '\n'.join(line['text'] for line in lines)
    else:
        text = raw[:64000] if isinstance(raw, str) else ''
    return {'available': bool(text), 'text': text, 'lines': lines,
            'source': str(result.get('source') or '')[:256]}


def synced_fallback(video, provider, playlist):
    """Use exact public metadata and duration to avoid matching another recording."""
    try:
        metadata = next((t for t in playlist.get('tracks', [])
                         if isinstance(t, dict) and t.get('videoId') == video), None)
        if not metadata:
            song = provider.get_song(video)
            metadata = song.get('videoDetails') if isinstance(song, dict) else None
        if not isinstance(metadata, dict):
            return None
        title = metadata.get('title')
        artists = metadata.get('artists')
        artist = metadata.get('author') or (artists[0].get('name') if isinstance(artists, list) and artists and isinstance(artists[0], dict) else '')
        duration = metadata.get('lengthSeconds') or metadata.get('duration_seconds')
        clock = metadata.get('length') or metadata.get('duration')
        if not duration and isinstance(clock, str):
            duration = sum(int(part) * 60 ** i for i, part in enumerate(reversed(clock.split(':'))))
        if not duration:
            song = provider.get_song(video)
            details = song.get('videoDetails', {}) if isinstance(song, dict) else {}
            duration = details.get('lengthSeconds')
        duration = float(duration or 0)
        if not title or not artist or not math.isfinite(duration) or duration <= 0:
            return None
        with requests.get('https://lrclib.net/api/get', params={
                'track_name':title, 'artist_name':artist, 'duration':round(duration)},
                headers={'User-Agent':'MusicBox/1.0 (browser synced lyrics)'}, timeout=(2, 4), stream=True) as response:
            if response.status_code != 200:
                return None
            payload = bytearray()
            for chunk in response.iter_content(16384):
                payload.extend(chunk)
                if len(payload) > 262144:
                    return None
            import json
            result = json.loads(payload)
        if abs(float(result.get('duration') or 0) - duration) > 5:
            return None
        raw = result.get('syncedLyrics')
        if not isinstance(raw, str):
            return None
        timed = []
        for row in raw.splitlines()[:2000]:
            stamps = list(re.finditer(r'\[(\d{1,3}):([0-5]\d(?:\.\d{1,3})?)\]', row))
            if not stamps:
                continue
            text = row[stamps[-1].end():].strip()
            if text:
                for stamp in stamps:
                    timed.append({'text':text, 'start_time':round((int(stamp[1]) * 60 + float(stamp[2])) * 1000)})
        normalized = normalize_lyrics({'lyrics':timed, 'source':'LRCLIB'})
        return normalized if normalized['lines'] else None
    except Exception:
        # Optional timing enrichment must never hide usable YouTube lyrics.
        return None


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
        if not normalized['lines']:
            normalized = synced_fallback(video, provider, playlist) or normalized
        with self.lock:
            self.entries[key] = (self.now() + (3600 if normalized['lines'] else 60), normalized)
            while len(self.entries) > self.limit:
                self.entries.popitem(last=False)
        return normalized
