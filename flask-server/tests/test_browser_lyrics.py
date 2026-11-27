import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase, mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from browser_lyrics import BrowserLyrics, normalize_lyrics


class BrowserLyricsTests(TestCase):
    def test_timed_dataclasses_are_normalized_and_sorted(self):
        result = normalize_lyrics({'lyrics':[SimpleNamespace(text='Later', start_time=2000),
                                            SimpleNamespace(text='First', start_time=0)], 'source':'Provider'})
        self.assertEqual(result['lines'], [{'text':'First','start_ms':0}, {'text':'Later','start_ms':2000}])
        self.assertEqual(result['text'], 'First\nLater')

    def test_missing_plain_and_oversized_lyrics(self):
        self.assertFalse(normalize_lyrics(None)['available'])
        self.assertEqual(normalize_lyrics({'lyrics':'hello'})['text'], 'hello')
        self.assertEqual(len(normalize_lyrics({'lyrics':'x'*100000})['text']),64000)

    def test_cache_is_bounded_and_never_crosses_account_identities(self):
        provider = mock.Mock()
        provider.get_watch_playlist.return_value = {'lyrics':'browse'}
        provider.get_lyrics.return_value = {'lyrics':'Alice lyrics'}
        cache = BrowserLyrics(limit=2, now=lambda:10)
        self.assertEqual(cache.get('alice','AAAAAAAAAAA',provider)['text'],'Alice lyrics')
        provider.get_lyrics.return_value = {'lyrics':'Bob lyrics'}
        self.assertEqual(cache.get('bob','AAAAAAAAAAA',provider)['text'],'Bob lyrics')
        self.assertEqual(cache.get('alice','AAAAAAAAAAA',provider)['text'],'Alice lyrics')
        cache.get('alice','BBBBBBBBBBB',provider)
        self.assertEqual(len(cache.entries),2)
        self.assertNotIn(('bob','AAAAAAAAAAA'),cache.entries)

    def test_plain_fallback_refresh_and_expiry(self):
        now=[10]
        provider=mock.Mock()
        provider.get_watch_playlist.return_value={'lyrics':'browse'}
        provider.get_lyrics.side_effect=[RuntimeError('timed unavailable'), {'lyrics':'plain'}, {'lyrics':'refreshed'}, {'lyrics':'after expiry'}]
        cache=BrowserLyrics(now=lambda:now[0])
        self.assertEqual(cache.get('alice','AAAAAAAAAAA',provider)['text'],'plain')
        self.assertEqual(cache.get('alice','AAAAAAAAAAA',provider,refresh=True)['text'],'refreshed')
        now[0]+=3601
        self.assertEqual(cache.get('alice','AAAAAAAAAAA',provider)['text'],'after expiry')

    def test_empty_timed_response_uses_plain_lyrics(self):
        provider=mock.Mock()
        provider.get_watch_playlist.return_value={'lyrics':'browse'}
        provider.get_lyrics.side_effect=[None,{'lyrics':'Plain lyrics'}]
        self.assertEqual(BrowserLyrics().get('alice','AAAAAAAAAAA',provider)['text'],'Plain lyrics')
