from typing import Dict, List, Optional, Tuple
from ask_sdk_model import Response
from ask_sdk_model.interfaces.audioplayer import PlayDirective, PlayBehavior, AudioItem, Stream, AudioItemMetadata, StopDirective
from ask_sdk_core.handler_input import HandlerInput
from ask_sdk_model.services.directive import (SendDirectiveRequest, Header, SpeakDirective)
import logging, random, urllib3, json, data, difflib
from ask_sdk_model.interfaces import display
from urllib.parse import urlencode
from xml.sax.saxutils import escape
from dacite import from_dict
from dataclasses import asdict
from decimal import Decimal
from models import player_models
import re

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
http = urllib3.PoolManager()

# Sliding playlist window. The whole persisted state lives in one DynamoDB item
# (hard 400KB cap), so the full 1000-track playlist can never be stored — a
# playlist that size made every save throw and killed playback at the first
# auto-advance. Instead we hold a bounded window of tracks and page the rest
# from the server (/queue_tracks/, seeded by our last track's video_id) as
# playback nears the window's end; trim_window drops already-played tracks
# beyond a small "previous" allowance to keep the item bounded forever.
_QUEUE_BATCH = 75        # tracks fetched per window request/extension
_MAX_STORED_TRACKS = 150  # hard bound on tracks kept in DynamoDB
_KEEP_BEHIND = 25        # played tracks kept for "previous" before trimming


def _window_playlist(playlist: List, index: int) -> Tuple[List, int]:
    """Clamp an incoming playlist to the storable window around index,
    returning (windowed_playlist, adjusted_index). Defensive: the server
    already windows its responses, but an older server returns everything."""
    if len(playlist) <= _MAX_STORED_TRACKS:
        return playlist, index
    start = max(0, index - _KEEP_BEHIND)
    return playlist[start:start + _MAX_STORED_TRACKS], index - start


def _coerce_decimals(value):
    """Recursively convert DynamoDB's Decimal (boto3's persistence adapter
    converts all numbers on save) back to int/float so dacite's strict typing
    doesn't reject persisted data on read."""
    if isinstance(value, Decimal):
        as_int = int(value)
        return as_int if as_int == value else float(value)
    if isinstance(value, dict):
        return {k: _coerce_decimals(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_coerce_decimals(v) for v in value]
    return value

# Leading token the web remote prepends to play queries so app-initiated
# playback runs silently. Must match AlexaRemote.SILENT_PLAY_MARKER on the
# Flask side. Detected case-insensitively as a leading whole word (Alexa's
# NLU lowercases and may add/trim surrounding whitespace).
SILENT_PLAY_MARKER = "silentmode"
_SILENT_MARKER_RE = re.compile(rf'^\s*{SILENT_PLAY_MARKER}\b[\s,]*', re.IGNORECASE)
DIRECT_VIDEO_MARKER = "silentid"
_DIRECT_VIDEO_RE = re.compile(
    rf'^\s*{DIRECT_VIDEO_MARKER}\b[\s,:-]*([a-f0-9]{{12,}})(?:\s+([a-f0-9]+))?\s*$',
    re.IGNORECASE)

# Short, NLU-safe phrase the web remote sends for a direct (armed) play. The
# exact video id is armed server-side and fetched via /armed_play/, so we only
# need to recognise this phrase — plain words survive Alexa's speech NLU intact,
# unlike the hex id the DIRECT_VIDEO_MARKER path relied on. Matched loosely
# (NLU may drop "the", add punctuation, or change case).
_APP_SELECTION_RE = re.compile(r'^\s*(?:the\s+)?app\s+selection\s*$', re.IGNORECASE)


def is_app_selection_request(query: str) -> bool:
    """True when the query is the web remote's armed-play trigger phrase."""
    return bool(query) and bool(_APP_SELECTION_RE.match(query))

def strip_silent_marker(query: str) -> Tuple[bool, str]:
    """Return (is_silent, cleaned_query). If the query starts with the silent
    marker, strip it and report True; otherwise pass the query through."""
    if not query:
        return False, query
    cleaned, n = _SILENT_MARKER_RE.subn('', query)
    if n:
        return True, cleaned.strip()
    return False, query

def direct_play_request(query: str):
    if not query:
        return None
    match = _DIRECT_VIDEO_RE.match(query)
    if not match:
        return None
    video_id = decode_hex(match.group(1))
    if not video_id or not re.fullmatch(r'[A-Za-z0-9_-]{6,}', video_id):
        return None
    offset_ms = 0
    if match.group(2):
        try:
            offset_ms = max(0, int(match.group(2), 16) * 1000)
        except ValueError:
            offset_ms = 0
    return video_id, offset_ms

def _normalise_volume(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number <= 1:
        number *= 100
    if 0 <= number <= 100:
        return int(round(number))
    return None

def _extract_volume(obj):
    if isinstance(obj, dict):
        for key, value in obj.items():
            if 'volume' not in str(key).lower():
                continue
            if isinstance(value, (dict, list)):
                if isinstance(value, dict):
                    for nested_key in ("value", "level", "percent", "volume"):
                        direct = _normalise_volume(value.get(nested_key))
                        if direct is not None:
                            return direct
                found = _extract_volume(value)
                if found is not None:
                    return found
            direct = _normalise_volume(value)
            if direct is not None:
                return direct
        for value in obj.values():
            if isinstance(value, (dict, list)):
                found = _extract_volume(value)
                if found is not None:
                    return found
    elif isinstance(obj, list):
        for item in obj:
            if isinstance(item, (dict, list)):
                found = _extract_volume(item)
                if found is not None:
                    return found
    return None

def _request_volume(handler_input):
    try:
        envelope = handler_input.request_envelope
        data_dict = envelope.to_dict() if hasattr(envelope, 'to_dict') else {}
        return _extract_volume(data_dict)
    except Exception:
        return None

def _notify_server(handler_input, event: str, **extra):
    """Fire-and-forget POST to the Flask server to report playback state changes.
    Never raises — errors are logged and swallowed so Alexa responses aren't delayed."""
    try:
        api_url, error = Attributes.get_api_url(handler_input)
        if error:
            logger.info(f'_notify_server: no api_url, skipping')
            return
        if 'volume' not in extra:
            volume = _request_volume(handler_input)
            if volume is not None:
                extra['volume'] = volume
        payload = json.dumps({'event': event, **extra})
        url = f'{api_url}/alexa/state_event/?key={data.API_KEY}'
        logger.info(f'_notify_server: POST {event} to {api_url}/alexa/state_event/')
        # 'started' is the web remote's only signal that the track changed on
        # auto-advance — a dropped POST wedges its now-playing card. Give it a
        # slightly longer timeout and one retry; other events stay cheap.
        timeout = urllib3.Timeout(total=3.0 if event == 'started' else 2.0)
        # allowed_methods=None: retry POST too (urllib3 excludes it by default);
        # re-delivering the same state event is harmless.
        retries = (urllib3.Retry(total=1, backoff_factor=0.3, allowed_methods=None)
                   if event == 'started' else False)
        resp = http.request(
            'POST', url,
            body=payload,
            headers={'Content-Type': 'application/json'},
            timeout=timeout,
            retries=retries,
        )
        logger.info(f'_notify_server: response {resp.status}')
    except Exception as e:
        logger.info(f'_notify_server FAILED: {e}')

def send_progressive_response(handler_input: HandlerInput, message: str):
    # Progressive responses are best-effort; a failure here (expired window,
    # directive service hiccup) must not abort the actual request handling.
    try:
        request_id_holder = handler_input.request_envelope.request.request_id
        directive_header = Header(request_id=request_id_holder)
        speech = SpeakDirective(speech=message)
        directive_request = SendDirectiveRequest(header=directive_header, directive=speech)
        directive_service_client = handler_input.service_client_factory.get_directive_service()
        directive_service_client.enqueue(directive_request)
    except Exception:
        logger.exception('Failed to send progressive response')
    return

# Real playlist ids / api urls hex-encode to well under this; a slot value
# anywhere near it is malformed input, not a legitimate longer URL.
_MAX_HEX_INPUT_LEN = 512

def decode_hex(encoded_string: str) -> str:
    # Returns None when the input is not a valid hex string (odd length,
    # non-hex characters, or unreasonably long) so callers can respond with
    # guidance instead of crashing or doing unbounded work on a slot value.
    lower_encoded_string = encoded_string.lower()
    if len(lower_encoded_string) > _MAX_HEX_INPUT_LEN:
        return None
    if len(lower_encoded_string) % 2 != 0:
        return None
    try:
        return ''.join([chr(int(lower_encoded_string[i:i+2], 16)) for i in range(0, len(lower_encoded_string), 2)])
    except ValueError:
        return None

def ssml_safe(text) -> str:
    # song/artist/playlist names can contain &, <, > which break SSML (XML)
    return escape(str(text))

def get_similarity(x: str, y: str):
    if not x or not y:
        return 0.0
    return difflib.SequenceMatcher(None, x.lower(), y.lower()).ratio()


class Attributes:
    @staticmethod
    def log_attributes(handler_input: HandlerInput):
        # Deliberately compact: dumping the full persistent attributes logged
        # the entire stored playlist (hundreds of KB) on every play/enqueue.
        try:
            user_attr = Attributes.get_user_attributes(handler_input) or {}
            playback_info = user_attr.get('playback_info') or {}
            logger.info(
                'attributes: playlist=%d index=%s play_order=%d offset_ms=%s enqueued=%s',
                len(user_attr.get('playlist') or []),
                playback_info.get('index'),
                len(playback_info.get('play_order') or []),
                playback_info.get('offset_in_ms'),
                playback_info.get('next_stream_enqueued'))
        except Exception:
            pass

    @staticmethod
    def get_user_id(handler_input: HandlerInput) -> str:
        user_id = handler_input.request_envelope.context.system.user.user_id
        return user_id

    @staticmethod
    def get_user_attributes(handler_input: HandlerInput) -> Dict:
        persistent_attr = handler_input.attributes_manager.persistent_attributes
        user_attr = persistent_attr.get(Attributes.get_user_id(handler_input))
        return user_attr
    
    @staticmethod
    def get_playback_info(handler_input: HandlerInput) -> Dict:
        user_attr = Attributes.get_user_attributes(handler_input)
        playback_info = user_attr.get('playback_info')
        return playback_info
    
    @staticmethod
    def get_playback_setting(handler_input: HandlerInput) -> Dict:
        user_attr = Attributes.get_user_attributes(handler_input)
        playback_setting = user_attr.get("playback_setting")
        return playback_setting

    @staticmethod
    def get_playlist(handler_input: HandlerInput) -> List[player_models.Metadata]:
        user_attr = Attributes.get_user_attributes(handler_input)
        # DynamoDB round-trips numbers as Decimal (boto3's persistence adapter
        # converts on save), but Metadata/Thumbnail's int fields are typed as
        # plain int and dacite's strict checking rejects Decimal — coerce back
        # before parsing.
        raw_playlist = [_coerce_decimals(item) for item in user_attr.get('playlist') or []]
        playlist = [from_dict(player_models.Metadata, i) for i in raw_playlist]
        return playlist
    
    @staticmethod
    def set_playlist(handler_input: HandlerInput, playlist: List[player_models.Metadata]) -> None:
        user_attr = Attributes.get_user_attributes(handler_input)
        user_attr['playlist'] = [asdict(i) for i in playlist]

    @staticmethod
    def get_play_order(handler_input: HandlerInput):
        playback_info = Attributes.get_playback_info(handler_input)
        return playback_info['play_order']

    @staticmethod
    def set_play_order(handler_input: HandlerInput) -> None:
        playback_setting = Attributes.get_playback_setting(handler_input)
        playback_info = Attributes.get_playback_info(handler_input)
        playlist = Attributes.get_playlist(handler_input)
        shuffle = playback_setting['shuffle']

        if shuffle:
            shuffled_play_order = Attributes.shuffle_order(handler_input)
            playback_info['play_order'] = shuffled_play_order
            shuffled_index_adjusted_play_order = Attributes.rotate_to_match_index(handler_input)
            playback_info["play_order"] = shuffled_index_adjusted_play_order
        else:
            playback_info["play_order"] = [l for l in range(0, len(playlist))]

    @staticmethod
    def get_from_saved_playlists(handler_input: HandlerInput, playlist_name: str) -> player_models.Playlist:
        if not playlist_name: return None
        user_attr = Attributes.get_user_attributes(handler_input)
        saved_playlists = user_attr.get('saved_playlists') or {}
        playlist = saved_playlists.get(playlist_name)
        logger.info(f'saved_playlists -> {saved_playlists}, playlist -> {playlist}, playlist_name -> {playlist_name}')
        if playlist: return from_dict(player_models.Playlist, playlist)

    @staticmethod
    def get_offset_in_ms(handler_input: HandlerInput):
        return handler_input.request_envelope.request.offset_in_milliseconds

    @staticmethod
    def get_token(handler_input: HandlerInput):
        return handler_input.request_envelope.request.token

    @staticmethod
    def get_metadata_by_play_order(handler_input: HandlerInput, index: int = None) -> player_models.Metadata:
        # Returns None when there is no playlist or the index is stale
        # (e.g. state overwritten from another device).
        playback_info = Attributes.get_playback_info(handler_input)
        if index is None: index = playback_info["index"]
        play_order = playback_info.get('play_order') or []
        playlist = Attributes.get_playlist(handler_input)
        if not playlist or index >= len(play_order):
            return None
        play_order_index = play_order[index]
        if play_order_index >= len(playlist):
            return None
        return playlist[play_order_index]

    @staticmethod
    def shuffle_order(handler_input: HandlerInput) -> List[int]:
        play_order = [l for l in range(0, len(Attributes.get_playlist(handler_input)))]
        random.shuffle(play_order)
        return play_order
    
    @staticmethod
    def rotate_to_match_index(handler_input: HandlerInput) -> List[int]:
        playback_info = Attributes.get_playback_info(handler_input)
        play_order = playback_info['play_order']
        current_index = playback_info['index']
        try:
            diff = play_order.index(current_index)-current_index
        except ValueError:
            # Stale index (playlist changed underneath); skip the rotation.
            return play_order
        return play_order[diff:]+play_order[:diff]
    
    @staticmethod
    def match_playlist_name(handler_input: HandlerInput, playlist_name: str, match_similarity: float = 0.7) -> str:
        if not playlist_name: return None
        user_attr = Attributes.get_user_attributes(handler_input)
        saved_playlists = user_attr.get('saved_playlists') or {}
        all_list = list(saved_playlists.keys())
        for name in all_list:
            similarity = get_similarity(name, playlist_name)
            logger.info(f'similarity -> {similarity}, left -> {name}, right -> {playlist_name}')
            if similarity > match_similarity: return name
        return None
    
    @staticmethod
    def get_audio_item_metadata(metadata: player_models.Metadata) -> AudioItemMetadata:
        if not metadata.thumbnail:
            return AudioItemMetadata(title=metadata.title, subtitle=metadata.artist)
        metadata = AudioItemMetadata(
            title=metadata.title,
            subtitle=metadata.artist,
            art=display.Image(
                content_description=metadata.title,
                sources=[
                    display.ImageInstance(
                        url=metadata.thumbnail.url)
                ]
            )
            , background_image=display.Image(
                content_description=metadata.title,
                sources=[
                    display.ImageInstance(
                        url=metadata.thumbnail.url)
                ]
            )
        )
        return metadata
    
    @staticmethod
    def get_calculated_index(handler_input: HandlerInput) -> int:
        current_video_id = handler_input.request_envelope.request.token
        playlist = Attributes.get_playlist(handler_input)
        play_order = Attributes.get_play_order(handler_input)
        playlist_tokens = [i.video_id for i in playlist]
        try:
            playlist_index = playlist_tokens.index(current_video_id)
            return play_order.index(playlist_index)
        except ValueError:
            # Token not in the stored playlist (state overwritten from another
            # device or a stale event); keep the current index instead of crashing.
            logger.warning(f'Token {current_video_id} not found in stored playlist; keeping current index.')
            return Attributes.get_playback_info(handler_input).get("index", 0)
    
    @staticmethod
    def get_api_url(handler_input: HandlerInput) -> Tuple[str, Exception]:
        user_attr = Attributes.get_user_attributes(handler_input)
        api_url = user_attr.get('api_url') or data.DEFAULT_API_URL
        if not api_url: return None, Exception(data.API_URL_NOT_SET)
        return api_url.rstrip('/'), None


class Api:
    @staticmethod
    def _get_json(handler_input: HandlerInput, path: str, params: Dict) -> Tuple[Dict, Exception]:
        api_url, error = Attributes.get_api_url(handler_input)
        if error: return None, error
        query = urlencode({**params, 'key': data.API_KEY})
        url = f"{api_url}/{path}/?{query}"
        try:
            response = http.request("GET", url, timeout=urllib3.Timeout(total=25.0))
        except Exception:
            logger.exception(f'Request to {path} failed')
            return None, Exception(data.API_CONNECTION_ISSUE)
        if response.status == 404:
            return None, Exception(data.NOT_FOUND)
        if response.status != 200:
            logger.error(f'{path} returned status {response.status}: {response.data[:500]}')
            return None, Exception(data.SERVICE_ISSUE)
        try:
            response_json = json.loads(response.data.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            logger.exception(f'{path} returned unparseable body')
            return None, Exception(data.SERVICE_ISSUE)
        if response_json is None:
            return None, Exception(data.NOT_FOUND)
        return response_json, None

    @staticmethod
    def find_stream_list(handler_input: HandlerInput, query: str, filter: player_models.Filter) -> Tuple[player_models.SongInfoList, Exception]:
        response_json, error = Api._get_json(handler_input, 'find_stream_list', {'query': query, 'filter': filter.value})
        if error: return None, error
        try:
            return from_dict(player_models.SongInfoList, response_json), None
        except Exception:
            logger.exception('find_stream_list returned unexpected shape')
            return None, Exception(data.SERVICE_ISSUE)

    @staticmethod
    def stream_playlist(handler_input: HandlerInput, playlist_id: str) -> Tuple[player_models.SongInfoList, Exception]:
        # limit: ask for a window only — the full playlist can't be persisted
        # (see the sliding-window notes at the top of this module). An older
        # server ignores it; _window_playlist clamps that case on our side.
        response_json, error = Api._get_json(handler_input, 'stream_playlist',
                                             {'id': playlist_id, 'limit': _QUEUE_BATCH})
        if error: return None, error
        try:
            return from_dict(player_models.SongInfoList, response_json), None
        except Exception:
            logger.exception('stream_playlist returned unexpected shape')
            return None, Exception(data.SERVICE_ISSUE)

    @staticmethod
    def stream_video(handler_input: HandlerInput, video_id: str) -> Tuple[player_models.SongInfoList, Exception]:
        response_json, error = Api._get_json(handler_input, 'stream_video', {'video_id': video_id})
        if error: return None, error
        try:
            return from_dict(player_models.SongInfoList, response_json), None
        except Exception:
            logger.exception('stream_video returned unexpected shape')
            return None, Exception(data.SERVICE_ISSUE)

    @staticmethod
    def get_stream(handler_input: HandlerInput, video_id: str) -> Tuple[player_models.Stream, Exception]:
        response_json, error = Api._get_json(handler_input, 'get_stream', {'video_id': video_id})
        if error: return None, error
        try:
            stream = from_dict(player_models.Stream, response_json)
        except Exception:
            logger.exception('get_stream returned unexpected shape')
            return None, Exception(data.SERVICE_ISSUE)
        player_info = Attributes.get_playback_info(handler_input)
        player_info['stream_url'] = stream.audio_url
        return stream, None

    @staticmethod
    def get_playlist_info(handler_input: HandlerInput, playlist_id: str) -> Tuple[player_models.Playlist, Exception]:
        response_json, error = Api._get_json(handler_input, 'get_playlist_info', {'id': playlist_id})
        if error: return None, error
        if not isinstance(response_json, dict) or 'id' not in response_json:
            return None, Exception(data.SERVICE_ISSUE)
        return player_models.Playlist(response_json['id'], response_json.get('title', 'Untitled')), None

    @staticmethod
    def get_radio(handler_input: HandlerInput, video_id: str) -> Tuple[List[player_models.Metadata], Exception]:
        """Radio/autoplay continuation for a seed video. The server returns the
        full queue (tracks 2+) so playback can continue past the seed track."""
        response_json, error = Api._get_json(handler_input, 'get_radio', {'video_id': video_id})
        if error: return None, error
        try:
            tracks = (response_json or {}).get('playlist') or []
            return [from_dict(player_models.Metadata, t) for t in tracks], None
        except Exception:
            logger.exception('get_radio returned unexpected shape')
            return None, Exception(data.SERVICE_ISSUE)

    @staticmethod
    def get_queue_tracks(handler_input: HandlerInput, after_video_id: str) -> Tuple[Optional[List[player_models.Metadata]], Optional[Exception]]:
        """Continuation batch for the sliding playlist window: the tracks that
        follow after_video_id in the server's current queue. Empty list when the
        server's queue moved on (caller falls back to radio continuation)."""
        response_json, error = Api._get_json(handler_input, 'queue_tracks',
                                             {'after': after_video_id, 'limit': _QUEUE_BATCH})
        if error: return None, error
        try:
            tracks = (response_json or {}).get('tracks') or []
            return [from_dict(player_models.Metadata, t) for t in tracks], None
        except Exception:
            logger.exception('queue_tracks returned unexpected shape')
            return None, Exception(data.SERVICE_ISSUE)

    @staticmethod
    def play_genre(handler_input: HandlerInput, genre: str) -> Tuple[Optional[player_models.SongInfoList], Optional[Exception]]:
        """Ask the server for a genre queue (it searches YT Music for a top
        '<genre> music' playlist and streams it). Same response shape as
        stream_playlist, so the result plugs into Controller.play directly."""
        response_json, error = Api._get_json(handler_input, 'play_genre',
                                             {'genre': genre, 'limit': _QUEUE_BATCH})
        if error: return None, error
        try:
            return from_dict(player_models.SongInfoList, response_json), None
        except Exception:
            logger.exception('play_genre returned unexpected shape')
            return None, Exception(data.SERVICE_ISSUE)

    @staticmethod
    def like_song(handler_input: HandlerInput, metadata: player_models.Metadata) -> Tuple[Optional[Dict], Optional[Exception]]:
        """Add a track to the web remote's Liked Songs playlist. Returns the
        server's {'ok': ..., 'already_liked': ...} payload."""
        params = {
            'video_id': metadata.video_id,
            'title': metadata.title or '',
            'artist': metadata.artist or '',
            'duration_ms': metadata.duration_ms or 0,
        }
        if metadata.thumbnail and metadata.thumbnail.url:
            params['thumbnail'] = metadata.thumbnail.url
        return Api._get_json(handler_input, 'alexa/like', params)

    @staticmethod
    def get_armed_play(handler_input: HandlerInput) -> Tuple[Optional[Tuple[str, int]], Optional[Exception]]:
        """Fetch the video id the web remote armed for a direct play. Returns
        ((video_id, offset_ms), None) when one is pending, else (None, error).
        The server returns the most-recently-armed play (see /armed_play/)."""
        response_json, error = Api._get_json(handler_input, 'armed_play', {})
        if error:
            return None, error
        video_id = (response_json or {}).get('video_id')
        if not video_id:
            return None, Exception(data.NOT_FOUND)
        try:
            offset_ms = max(0, int((response_json or {}).get('offset_ms') or 0))
        except (TypeError, ValueError):
            offset_ms = 0
        return (video_id, offset_ms), None


class Controller:
    @staticmethod
    def error_response(handler_input: HandlerInput, message, is_playback: bool = False) -> Response:
        if is_playback:
            return handler_input.response_builder.set_should_end_session(True).response
        return handler_input.response_builder.speak(str(message)).response

    @staticmethod
    def fetch(
        handler_input: HandlerInput,
        query: str = None,
        playlist_id: str = None,
        genre: str = None,
        filter: player_models.Filter = player_models.Filter.SONGS,
        is_playback: bool = True
    ) -> Response:
        if genre:
            song_info_list, error = Api.play_genre(handler_input, genre)
            if error:
                # No suitable genre playlist (or older server without the
                # endpoint): fall back to a plain song search for the genre.
                song_info_list, error = Api.find_stream_list(
                    handler_input, f'{genre} music', player_models.Filter.SONGS)
            if error: return Controller.error_response(handler_input, error, is_playback)
            playlist = song_info_list.playlist
            song_info = song_info_list.song_info
        elif query:
            song_info_list, error = Api.find_stream_list(handler_input, query, filter)
            if error: return Controller.error_response(handler_input, error, is_playback)
            playlist = song_info_list.playlist
            song_info = song_info_list.song_info
        else:
            song_info_list, error = Api.stream_playlist(handler_input, playlist_id)
            if error: return Controller.error_response(handler_input, error, is_playback)
            playlist = song_info_list.playlist
            song_info = song_info_list.song_info

        playlist, _ = _window_playlist(playlist, 0)
        user_attr = Attributes.get_user_attributes(handler_input)
        Attributes.set_playlist(handler_input, playlist)
        user_attr['playback_info'] = {
            'index': 0,
            'offset_in_ms': 0,
            'play_order': [l for l in range(0, len(playlist))],
            'stream_url': song_info.stream.audio_url
        }
        Attributes.set_play_order(handler_input)
        return Controller.play(handler_input, song_info, is_playback)

    @staticmethod
    def fetch_video_id(
        handler_input: HandlerInput,
        video_id: str,
        is_playback: bool = True,
        offset_in_ms: int = 0
    ) -> Response:
        song_info_list, error = Api.stream_video(handler_input, video_id)
        if error:
            return Controller.error_response(handler_input, error, is_playback)
        playlist = song_info_list.playlist or [song_info_list.song_info.metadata]
        song_info = song_info_list.song_info
        index = next((i for i, item in enumerate(playlist) if item.video_id == video_id), 0)
        playlist, index = _window_playlist(playlist, index)

        user_attr = Attributes.get_user_attributes(handler_input)
        Attributes.set_playlist(handler_input, playlist)
        user_attr['playback_info'] = {
            'index': index,
            'offset_in_ms': max(0, int(offset_in_ms or 0)),
            'play_order': [l for l in range(0, len(playlist))],
            'stream_url': song_info.stream.audio_url
        }
        Attributes.set_play_order(handler_input)
        return Controller.play(handler_input, song_info, is_playback)

    @staticmethod
    def expand_radio_queue(handler_input: HandlerInput) -> bool:
        """Lazily fill the autoplay queue from the currently-playing seed track.

        find_stream_list now returns only the seed (fast start); this fetches the
        radio continuation once playback has begun so tracks 2+ are ready before
        the seed nearly finishes. No-op (returns False) if the queue already has
        more than one track. Never raises."""
        try:
            playlist = Attributes.get_playlist(handler_input)
            if not playlist or len(playlist) > 1:
                return False  # already expanded, or nothing to expand
            seed = playlist[0]
            radio, error = Api.get_radio(handler_input, seed.video_id)
            if error or not radio:
                logger.info(f'expand_radio_queue: no queue ({error})')
                return False
            # Radio queue is seeded from the same video, so its first track is
            # usually the seed itself — drop duplicates of what we already have.
            have = {seed.video_id}
            merged = [seed]
            for m in radio:
                if m.video_id and m.video_id not in have:
                    merged.append(m)
                    have.add(m.video_id)
            if len(merged) <= 1:
                return False
            Attributes.set_playlist(handler_input, merged)
            # Rebuild play order to cover the newly-added tracks (index stays 0).
            Attributes.set_play_order(handler_input)
            logger.info(f'expand_radio_queue: playlist now {len(merged)} tracks')
            return True
        except Exception:
            logger.exception('expand_radio_queue failed')
            return False

    @staticmethod
    def _append_tracks(handler_input: HandlerInput, new_tracks: List[player_models.Metadata]) -> bool:
        """Append already-deduped tracks to playlist + play_order in place
        (does not touch index or any already-played ordering). New tracks are
        shuffled among themselves when shuffle is on."""
        if not new_tracks:
            return False
        playlist = Attributes.get_playlist(handler_input)
        base = len(playlist)
        Attributes.set_playlist(handler_input, playlist + new_tracks)

        playback_info = Attributes.get_playback_info(handler_input)
        playback_setting = Attributes.get_playback_setting(handler_input)
        new_indices = list(range(base, base + len(new_tracks)))
        if playback_setting.get('shuffle'):
            random.shuffle(new_indices)
        playback_info['play_order'] = list(playback_info.get('play_order') or []) + new_indices
        return True

    @staticmethod
    def _dedupe_against_playlist(playlist: List[player_models.Metadata],
                                 candidates: List[player_models.Metadata]) -> List[player_models.Metadata]:
        have = {m.video_id for m in playlist if m.video_id}
        new_tracks = []
        for m in candidates:
            if m.video_id and m.video_id not in have:
                new_tracks.append(m)
                have.add(m.video_id)
        return new_tracks

    @staticmethod
    def extend_queue(handler_input: HandlerInput) -> bool:
        """Grow the playlist when playback reaches the end of the stored window.

        Tries the server-queue continuation first (/queue_tracks/ after our
        last track — this is how a windowed 1000-track playlist keeps playing
        in order), then falls back to radio continuation. Trims already-played
        tracks afterwards so the DynamoDB item stays bounded. Never raises.
        Returns True iff new tracks were appended."""
        grew = False
        try:
            playlist = Attributes.get_playlist(handler_input)
            if playlist and playlist[-1].video_id:
                tracks, error = Api.get_queue_tracks(handler_input, playlist[-1].video_id)
                if error or not tracks:
                    logger.info(f'extend_queue: no server-queue continuation ({error})')
                else:
                    grew = Controller._append_tracks(
                        handler_input, Controller._dedupe_against_playlist(playlist, tracks))
                    if grew:
                        logger.info('extend_queue: extended from server queue')
        except Exception:
            logger.exception('extend_queue: server-queue continuation failed')
        if not grew:
            grew = Controller.extend_radio_queue(handler_input)
        if grew:
            Controller.trim_window(handler_input)
        return grew

    @staticmethod
    def extend_radio_queue(handler_input: HandlerInput) -> bool:
        """Grow the playlist with another batch of radio continuation once the
        end of the current queue is reached. Seeded from the last track already
        in the playlist so YT Music keeps the recommendation thread going.
        Never raises. Returns True iff new tracks were appended."""
        try:
            playlist = Attributes.get_playlist(handler_input)
            if not playlist:
                return False
            seed = playlist[-1]
            if not seed.video_id:
                return False
            radio, error = Api.get_radio(handler_input, seed.video_id)
            if error or not radio:
                logger.info(f'extend_radio_queue: no continuation ({error})')
                return False
            new_tracks = Controller._dedupe_against_playlist(playlist, radio)
            if not new_tracks:
                logger.info('extend_radio_queue: nothing new to append')
                return False
            # Bound growth per extension; trim_window keeps the overall size in
            # check, so radio can extend indefinitely.
            if not Controller._append_tracks(handler_input, new_tracks[:25]):
                return False
            logger.info(f'extend_radio_queue: appended {len(new_tracks[:25])} tracks')
            return True
        except Exception:
            logger.exception('extend_radio_queue failed')
            return False

    @staticmethod
    def trim_window(handler_input: HandlerInput) -> None:
        """Drop long-played tracks so the stored playlist never exceeds
        _MAX_STORED_TRACKS (the whole persisted state must fit DynamoDB's 400KB
        item cap). Keeps _KEEP_BEHIND already-played tracks for "previous".
        Remaps play_order/index to the trimmed playlist. Never raises."""
        try:
            playback_info = Attributes.get_playback_info(handler_input)
            playlist = Attributes.get_playlist(handler_input)
            excess = len(playlist) - _MAX_STORED_TRACKS
            if excess <= 0:
                return
            play_order = list(playback_info.get('play_order') or [])
            index = int(playback_info.get('index', 0) or 0)
            if len(play_order) != len(playlist):
                # Inconsistent state (shouldn't happen): rebuild a sequential
                # order rather than guessing which entries map where.
                play_order = list(range(len(playlist)))
                index = min(max(index, 0), len(playlist) - 1)
            # Only play_order positions well behind the needle are trimmable.
            drop_n = min(excess, max(0, index - _KEEP_BEHIND))
            if drop_n <= 0:
                return
            dropped = set(play_order[:drop_n])
            remap = {}
            new_i = 0
            for old_i in range(len(playlist)):
                if old_i in dropped:
                    continue
                remap[old_i] = new_i
                new_i += 1
            Attributes.set_playlist(
                handler_input, [m for i, m in enumerate(playlist) if i not in dropped])
            playback_info['play_order'] = [remap[i] for i in play_order[drop_n:]]
            playback_info['index'] = index - drop_n
            logger.info(f'trim_window: dropped {drop_n} tracks, playlist now {new_i}')
        except Exception:
            logger.exception('trim_window failed')

    @staticmethod
    def play(
        handler_input: HandlerInput,
        song_info: player_models.SongInfo,
        is_playback: bool = False,
        play_behavior: PlayBehavior = PlayBehavior.REPLACE_ALL
    ) -> Response:
        response_builder = handler_input.response_builder

        playback_info = Attributes.get_playback_info(handler_input)
        offset_in_ms = playback_info.get('offset_in_ms')
        if play_behavior == PlayBehavior.REPLACE_ALL: playback_info['next_stream_enqueued'] = False
        else: playback_info['next_stream_enqueued'] = True

        # Log all attrubutes ----------------------------------
        Attributes.log_attributes(handler_input)
        # -----------------------------------------------------

        response_builder.add_directive(
            PlayDirective(
                play_behavior=play_behavior,
                audio_item=AudioItem(
                    stream=Stream(
                        token=song_info.metadata.video_id,
                        url=song_info.stream.audio_url,
                        offset_in_milliseconds=offset_in_ms,
                        expected_previous_token=None),
                    metadata=Attributes.get_audio_item_metadata(song_info.metadata)))
        ).set_should_end_session(True)
        if not is_playback:
            response_builder.speak(data.PLAYBACK_PLAY.format(ssml_safe(song_info.metadata.title), ssml_safe(song_info.metadata.artist)))
        return response_builder.response

    @staticmethod
    def stop(handler_input: HandlerInput) -> Response:
        playback_info = Attributes.get_playback_info(handler_input)
        playback_info['in_playback_session'] = False
        # StopDirective clears Alexa's AudioPlayer queue, but our own session
        # attributes don't know that -- if PlaybackNearlyFinished had already
        # enqueued the next track before this stop, next_stream_enqueued would
        # stay True and PlaybackNearlyFinished would then skip re-enqueuing on
        # the next real playback, leaving no track queued for the *actual*
        # next auto-advance.
        playback_info['next_stream_enqueued'] = False

        handler_input.response_builder.add_directive(StopDirective())
        return handler_input.response_builder.response
    
    @staticmethod
    def pause(handler_input: HandlerInput) -> Response:
        return Controller.stop(handler_input)

    
    @staticmethod
    def resume(handler_input: HandlerInput, is_playback=False) -> Response:
        playback_info = Attributes.get_playback_info(handler_input)
        playback_info["next_stream_enqueued"] = False

        metadata = Attributes.get_metadata_by_play_order(handler_input)
        if not metadata:
            return Controller.error_response(handler_input, data.NOTHING_TO_RESUME, is_playback)
        if playback_info.get('stream_url'): stream = player_models.Stream(playback_info.get('stream_url'))
        else:
            stream, error = Api.get_stream(handler_input, metadata.video_id)
            if error: return Controller.error_response(handler_input, error, is_playback)
        song_info = player_models.SongInfo(metadata, stream)

        return Controller.play(handler_input, song_info, is_playback=is_playback)

    @staticmethod
    def seek(handler_input: HandlerInput, offset_in_ms: int, is_playback=True) -> Response:
        """Jump the current track to offset_in_ms. Alexa's AudioPlayer has no
        seek directive, so we re-issue a PlayDirective (REPLACE_ALL) for the same
        track starting at the requested offset. The stream re-buffers from there."""
        metadata = Attributes.get_metadata_by_play_order(handler_input)
        if not metadata:
            return Controller.error_response(handler_input, data.NOTHING_TO_RESUME, is_playback)

        playback_info = Attributes.get_playback_info(handler_input)
        if offset_in_ms < 0:
            offset_in_ms = 0
        playback_info["offset_in_ms"] = offset_in_ms
        playback_info["next_stream_enqueued"] = False

        # Reuse the already-resolved stream url when we have it; only hit the API
        # when we must (avoids a round-trip on every scrub).
        if playback_info.get('stream_url'):
            stream = player_models.Stream(playback_info.get('stream_url'))
        else:
            stream, error = Api.get_stream(handler_input, metadata.video_id)
            if error:
                return Controller.error_response(handler_input, error, is_playback)
        song_info = player_models.SongInfo(metadata, stream)
        return Controller.play(handler_input, song_info, is_playback=is_playback)

    @staticmethod
    def play_next(handler_input: HandlerInput, is_playback=False) -> Response:
        playlist = Attributes.get_playlist(handler_input)
        if not playlist:
            return Controller.error_response(handler_input, data.NOTHING_TO_RESUME, is_playback)
        playback_info = Attributes.get_playback_info(handler_input)
        playback_setting = Attributes.get_playback_setting(handler_input)
        current_index = playback_info.get("index", 0)
        next_index = (current_index + 1) % len(playlist)

        if next_index == 0 and not playback_setting.get("loop"):
            if Controller.extend_queue(handler_input):
                playlist = Attributes.get_playlist(handler_input)
                # extend_queue may have trimmed played tracks, shifting index.
                next_index = playback_info.get("index", 0) + 1
            else:
                if not is_playback:
                    handler_input.response_builder.speak(data.PLAYBACK_NEXT_END)

                return handler_input.response_builder.add_directive(
                    StopDirective()).response

        playback_info["index"] = next_index
        playback_info["offset_in_ms"] = 0
        playback_info["next_stream_enqueued"] = False

        metadata = Attributes.get_metadata_by_play_order(handler_input)
        if not metadata:
            return Controller.error_response(handler_input, data.NOTHING_TO_RESUME, is_playback)
        stream, error = Api.get_stream(handler_input, metadata.video_id)
        if error: return Controller.error_response(handler_input, error, is_playback)
        song_info = player_models.SongInfo(metadata, stream)

        return Controller.play(handler_input, song_info, is_playback=is_playback)

    @staticmethod
    def play_previous(handler_input: HandlerInput, is_playback=False) -> Response:
        playlist = Attributes.get_playlist(handler_input)
        if not playlist:
            return Controller.error_response(handler_input, data.NOTHING_TO_RESUME, is_playback)
        playback_info = Attributes.get_playback_info(handler_input)
        playback_setting = Attributes.get_playback_setting(handler_input)
        prev_index = playback_info.get("index", 0) - 1

        if prev_index == -1:
            if playback_setting.get("loop"):
                prev_index += len(playlist)
            else:
                if not is_playback:
                    handler_input.response_builder.speak(
                        data.PLAYBACK_PREVIOUS_END)

                return handler_input.response_builder.add_directive(
                    StopDirective()).response

        playback_info["index"] = prev_index
        playback_info["offset_in_ms"] = 0
        playback_info["next_stream_enqueued"] = False

        metadata = Attributes.get_metadata_by_play_order(handler_input)
        if not metadata:
            return Controller.error_response(handler_input, data.NOTHING_TO_RESUME, is_playback)
        stream, error = Api.get_stream(handler_input, metadata.video_id)
        if error: return Controller.error_response(handler_input, error, is_playback)
        song_info = player_models.SongInfo(metadata, stream)

        return Controller.play(handler_input, song_info, is_playback=is_playback)
