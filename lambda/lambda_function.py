import logging, os, boto3, data, re
import ask_sdk_core.utils as ask_utils
from ask_sdk_core.skill_builder import CustomSkillBuilder
from ask_sdk_core.api_client import DefaultApiClient
from ask_sdk_core.dispatch_components import AbstractRequestHandler, AbstractExceptionHandler, AbstractResponseInterceptor, AbstractRequestInterceptor
from ask_sdk_core.handler_input import HandlerInput
from ask_sdk_model.interfaces.audioplayer import PlayDirective, PlayBehavior, AudioItem, Stream
from ask_sdk_dynamodb.adapter import DynamoDbAdapter
from ask_sdk_model import Response
from mediaUtils import player
from dataclasses import asdict
from models import player_models

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

ddb_region = os.environ.get('DYNAMODB_PERSISTENCE_REGION')
ddb_table_name = os.environ.get('DYNAMODB_PERSISTENCE_TABLE_NAME')
ddb_resource = boto3.resource('dynamodb', region_name=ddb_region)
dynamodb_adapter = DynamoDbAdapter(table_name=ddb_table_name, create_table=False, dynamodb_resource=ddb_resource)

sb = CustomSkillBuilder(persistence_adapter = dynamodb_adapter, api_client=DefaultApiClient())

class LaunchRequestHandler(AbstractRequestHandler):
    def can_handle(self, handler_input: HandlerInput) -> Response:
        return ask_utils.is_request_type("LaunchRequest")(handler_input)

    def handle(self, handler_input: HandlerInput) -> Response:
        logger.info("In LaunchRequestHandler")
        speak_output = "This is your d. j. Say 'help' to know how I can help."
        return (
            handler_input.response_builder
                .speak(speak_output)
                .ask(speak_output)
                .response
        )
    
class PlaySongIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input: HandlerInput):
        return ask_utils.is_intent_name("PlaySongIntent")(handler_input)
    
    def handle(self, handler_input: HandlerInput):
        logger.info("In PlaySongIntentHandler")
        slots = handler_input.request_envelope.request.intent.slots
        song_name = slots['songName'].value
        if not song_name: return handler_input.response_builder.speak('Please say a song name, for example, "Alexa, ask DJ to play Blinding Lights"').response

        # The web remote prepends a marker so app-initiated plays run silently
        # (no "Searching..."/"Playing X by Y" speech). Voice commands lack it
        # and keep their spoken confirmation.
        silent, song_name = player.strip_silent_marker(song_name)

        # Web-remote direct play: the remote armed an exact video id server-side
        # and sent this short trigger phrase (the hex-encoded id path below is
        # kept only as a legacy fallback — Alexa's NLU mangles the encoded id).
        if player.is_app_selection_request(song_name):
            armed, error = player.Api.get_armed_play(handler_input)
            if error or not armed:
                return player.Controller.error_response(
                    handler_input, error or Exception(data.NOT_FOUND), is_playback=True)
            armed_video_id, armed_offset_ms = armed
            return player.Controller.fetch_video_id(
                handler_input=handler_input,
                video_id=armed_video_id,
                is_playback=True,
                offset_in_ms=armed_offset_ms
            )

        direct_play = player.direct_play_request(song_name)
        if direct_play:
            direct_video_id, offset_in_ms = direct_play
            return player.Controller.fetch_video_id(
                handler_input=handler_input,
                video_id=direct_video_id,
                is_playback=True,
                offset_in_ms=offset_in_ms
            )

        # Check if the said song is actually a playlist
        playlist_name = player.Attributes.match_playlist_name(handler_input, song_name)
        if playlist_name:
            playlist = player.Attributes.get_from_saved_playlists(handler_input, playlist_name)
            if playlist:
                if not silent:
                    player.send_progressive_response(handler_input, f'Starting playlist {playlist_name}.')
                return player.Controller.fetch(handler_input, playlist_id=playlist.id, is_playback=silent)

        if not silent:
            player.send_progressive_response(handler_input, 'Searching...')
        return player.Controller.fetch(
            handler_input=handler_input,
            query=song_name,
            filter=player_models.Filter.SONGS,
            is_playback=silent
        )
    
class PlayArtistIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input: HandlerInput):
        return ask_utils.is_intent_name("PlayArtistIntent")(handler_input)
    
    def handle(self, handler_input: HandlerInput):
        logger.info("In PlayArtistIntentHandler")
        slots = handler_input.request_envelope.request.intent.slots
        query = slots.get('artistName').value
        if not query: return handler_input.response_builder.speak('For artists, say, "Alexa, ask DJ to play song by The Weekend"').response
        player.send_progressive_response(handler_input, 'Searching artist...')
        return player.Controller.fetch(
            handler_input=handler_input, 
            query=query,
            filter=player_models.Filter.ARTISTS,
            is_playback=False
        )
    
class PlayAlbumIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input: HandlerInput):
        return ask_utils.is_intent_name("PlayAlbumIntent")(handler_input)
    
    def handle(self, handler_input: HandlerInput):
        logger.info("In PlaySongIntentHandler")
        slots = handler_input.request_envelope.request.intent.slots
        query = slots.get('albumName').value
        if not query: return handler_input.response_builder.speak('For albums, say, "Alexa, ask DJ to play album Thriller"').response
        player.send_progressive_response(handler_input, 'Searching album...')
        return player.Controller.fetch(
            handler_input=handler_input, 
            query=query,
            filter=player_models.Filter.ALBUMS,
            is_playback=False
        )
    
class StartPlaybackHandler(AbstractRequestHandler):
    def can_handle(self, handler_input: HandlerInput) -> Response:
        return (ask_utils.is_intent_name("AMAZON.ResumeIntent")(handler_input)
                or ask_utils.is_intent_name("PlayAudio")(handler_input))

    def handle(self, handler_input):
        logger.info("In StartPlaybackHandler")
        # A track already actively playing means this "play"/"resume" wasn't
        # resuming from a pause at all -- resume() always re-issues a fresh
        # PlayDirective from the last *stopped* offset (stale/0 if nothing was
        # ever paused this session), so calling it while already playing
        # restarted the current track from that stale point instead of being
        # a harmless no-op. Guard it here rather than in resume() itself,
        # since other callers (e.g. the app's explicit play button after a
        # real pause) still need the restart-from-offset behavior.
        if player.Attributes.get_playback_info(handler_input).get('in_playback_session'):
            return handler_input.response_builder.set_should_end_session(True).response
        # No spoken "Resuming..." — resume is triggered from the app's play
        # button and should be silent; the audio resuming is feedback enough.
        return player.Controller.resume(handler_input=handler_input, is_playback=True)
    
class PausePlaybackHandler(AbstractRequestHandler):
    def can_handle(self, handler_input: HandlerInput) -> Response:
        return ask_utils.is_intent_name("AMAZON.PauseIntent")(handler_input)

    def handle(self, handler_input: HandlerInput) -> Response:
        logger.info("In PausePlaybackHandler")
        return player.Controller.pause(handler_input)
    
class StopPlaybackHandler(AbstractRequestHandler):
    def can_handle(self, handler_input: HandlerInput) -> Response:
        return (ask_utils.is_intent_name("AMAZON.StopIntent")(handler_input))

    def handle(self, handler_input: HandlerInput) -> Response:
        logger.info("In StopPlaybackHandler")
        return player.Controller.pause(handler_input)
    
class NextPlaybackHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return ask_utils.is_intent_name("AMAZON.NextIntent")(handler_input)

    def handle(self, handler_input):
        # type: (HandlerInput) -> Response
        logger.info("In NextPlaybackHandler")
        return player.Controller.play_next(handler_input, is_playback=player.Attributes.get_playback_info(handler_input).get('in_playback_session'))
    
class PreviousPlaybackHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return ask_utils.is_intent_name("AMAZON.PreviousIntent")(handler_input)

    def handle(self, handler_input):
        logger.info("In PreviousPlaybackHandler")
        return player.Controller.play_previous(handler_input, is_playback=player.Attributes.get_playback_info(handler_input).get('in_playback_session'))

def _slot_int(slots, name):
    """Numeric slot value as int, or None when absent/unparseable."""
    slot = slots.get(name) if slots else None
    value = getattr(slot, 'value', None)
    try:
        return int(value)
    except (TypeError, ValueError):
        return None

def _current_offset_ms(handler_input: HandlerInput) -> int:
    """Best-known playback position: Alexa reports the live AudioPlayer offset
    in the request context while the skill's audio is playing; fall back to the
    last persisted offset (updated on pause/stop) when it's absent."""
    audio_player = handler_input.request_envelope.context.audio_player
    if audio_player and audio_player.offset_in_milliseconds is not None:
        return int(audio_player.offset_in_milliseconds)
    playback_info = player.Attributes.get_playback_info(handler_input)
    return int(playback_info.get('offset_in_ms', 0) or 0)

class SeekIntentHandler(AbstractRequestHandler):
    """Seek the current track to an absolute position. Triggered by voice
    ("skip to 2 minutes 30 seconds") and by the web remote's scrubber, which
    sends "ask music box to seek to N seconds" as a spoken-style command (the
    same routing transport buttons use)."""
    def can_handle(self, handler_input):
        return ask_utils.is_intent_name("SeekIntent")(handler_input)

    def handle(self, handler_input: HandlerInput):
        logger.info("In SeekIntentHandler")
        slots = handler_input.request_envelope.request.intent.slots
        minutes = _slot_int(slots, 'minutes')
        seconds = _slot_int(slots, 'seconds')
        if minutes is None and seconds is None:
            return handler_input.response_builder.speak('Say a position to skip to, for example, "skip to 2 minutes 30 seconds".').response
        total_seconds = (minutes or 0) * 60 + (seconds or 0)
        # Seek is only meaningful when something is playing; without a playlist
        # there is nothing to reposition.
        if not player.Attributes.get_playlist(handler_input):
            return handler_input.response_builder.speak(data.NOTHING_TO_RESUME).response
        # Silent: no spoken confirmation, the audio jumping is feedback enough
        # (and the web remote's scrubber relies on a speech-free response).
        return player.Controller.seek(handler_input, offset_in_ms=total_seconds * 1000, is_playback=True)

class RelativeSeekHandlerBase(AbstractRequestHandler):
    """Shared logic for fast-forward/rewind: shift from the live playback
    offset by the spoken amount (default 30 seconds)."""
    direction = 1  # +1 forward, -1 back

    def handle(self, handler_input: HandlerInput):
        slots = handler_input.request_envelope.request.intent.slots
        minutes = _slot_int(slots, 'minutes')
        seconds = _slot_int(slots, 'seconds')
        if minutes is None and seconds is None:
            delta_seconds = 30
        else:
            delta_seconds = (minutes or 0) * 60 + (seconds or 0)
        if not player.Attributes.get_playlist(handler_input):
            return handler_input.response_builder.speak(data.NOTHING_TO_RESUME).response
        new_offset = _current_offset_ms(handler_input) + self.direction * delta_seconds * 1000
        # Fast-forwarding past the end would re-issue the stream at an offset
        # beyond its length; do what the listener meant and go to the next
        # track instead. Only when the duration is actually known.
        metadata = player.Attributes.get_metadata_by_play_order(handler_input)
        duration_ms = int(getattr(metadata, 'duration_ms', 0) or 0) if metadata else 0
        if self.direction > 0 and duration_ms and new_offset >= duration_ms:
            return player.Controller.play_next(handler_input, is_playback=True)
        return player.Controller.seek(handler_input, offset_in_ms=max(0, new_offset), is_playback=True)

class FastForwardIntentHandler(RelativeSeekHandlerBase):
    direction = 1
    def can_handle(self, handler_input):
        return ask_utils.is_intent_name("FastForwardIntent")(handler_input)

    def handle(self, handler_input: HandlerInput):
        logger.info("In FastForwardIntentHandler")
        return super().handle(handler_input)

class RewindIntentHandler(RelativeSeekHandlerBase):
    direction = -1
    def can_handle(self, handler_input):
        return ask_utils.is_intent_name("RewindIntent")(handler_input)

    def handle(self, handler_input: HandlerInput):
        logger.info("In RewindIntentHandler")
        return super().handle(handler_input)

class LikeSongIntentHandler(AbstractRequestHandler):
    """Add the currently playing track to the web remote's Liked Songs
    playlist (the server's local 'liked' playlist, shown on the website)."""
    def can_handle(self, handler_input):
        return ask_utils.is_intent_name("LikeSongIntent")(handler_input)

    def handle(self, handler_input: HandlerInput):
        logger.info("In LikeSongIntentHandler")
        metadata = player.Attributes.get_metadata_by_play_order(handler_input)
        if not metadata:
            return handler_input.response_builder.speak('Nothing is playing right now, so there is no song to like.').response
        response, error = player.Api.like_song(handler_input, metadata)
        if error:
            return handler_input.response_builder.speak(str(error)).response
        title = player.ssml_safe(metadata.title)
        if response.get('already_liked'):
            return handler_input.response_builder.speak(f'{title} is already in your liked songs.').response
        return handler_input.response_builder.speak(f'Added {title} to your liked songs.').response

class PlayGenreIntentHandler(AbstractRequestHandler):
    """Play music of a genre: the server finds a top '<genre> music' playlist
    on YT Music and streams it (falls back to a plain song search)."""
    def can_handle(self, handler_input):
        return ask_utils.is_intent_name("PlayGenreIntent")(handler_input)

    def handle(self, handler_input: HandlerInput):
        logger.info("In PlayGenreIntentHandler")
        slots = handler_input.request_envelope.request.intent.slots
        genre = slots.get('genre').value if slots and slots.get('genre') else None
        if not genre:
            return handler_input.response_builder.speak('Say a genre to play, for example, "play some jazz music".').response
        player.send_progressive_response(handler_input, f'Finding {player.ssml_safe(genre)} music...')
        return player.Controller.fetch(handler_input, genre=genre, is_playback=False)

class LoopOnHandler(AbstractRequestHandler):
    """Handler for setting the audio loop on."""
    def can_handle(self, handler_input: HandlerInput) -> Response:
        return ask_utils.is_intent_name("AMAZON.LoopOnIntent")(handler_input)

    def handle(self, handler_input: HandlerInput) -> Response:
        logger.info("In LoopOnHandler")
        playback_setting = player.Attributes.get_playback_info(handler_input)
        playback_setting["loop"] = True

        return handler_input.response_builder.speak(data.LOOP_ON_MSG).response

class LoopOffHandler(AbstractRequestHandler):
    def can_handle(self, handler_input: HandlerInput) -> Response:
        return ask_utils.is_intent_name("AMAZON.LoopOffIntent")(handler_input)

    def handle(self, handler_input: HandlerInput) -> Response:
        logger.info("In LoopOffHandler")
        playback_setting = player.Attributes.get_playback_info(handler_input)
        playback_setting["loop"] = False

        return handler_input.response_builder.speak(data.LOOP_OFF_MSG).response

class ShuffleOnHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return ask_utils.is_intent_name("AMAZON.ShuffleOnIntent")(handler_input)

    def handle(self, handler_input: HandlerInput):
        logger.info("In ShuffleOnHandler")
        playback_setting = player.Attributes.get_playback_setting(handler_input)

        playback_setting["shuffle"] = True
        player.Attributes.set_play_order(handler_input)
        return handler_input.response_builder.speak('Shuffle On').response

class ShuffleOffHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):

        return ask_utils.is_intent_name("AMAZON.ShuffleOffIntent")(handler_input)

    def handle(self, handler_input: HandlerInput):
        logger.info("In ShuffleOffHandler")
        playback_setting = player.Attributes.get_playback_setting(handler_input)

        playback_setting["shuffle"] = False
        player.Attributes.set_play_order(handler_input)

        return handler_input.response_builder.speak('Shuffle Off').response

class StartOverHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return ask_utils.is_intent_name("AMAZON.StartOverIntent")(handler_input)

    def handle(self, handler_input: HandlerInput):
        logger.info("In StartOverHandler")
        playback_info = player.Attributes.get_playback_info(handler_input)
        playback_info["offset_in_ms"] = 0
        player.send_progressive_response(handler_input, 'Starting over...')
        # return player.Controller.fetch(
        #     handler_input=handler_input
        # )
        return handler_input.response_builder.speak('This feature is not available yet.').response
    
class AnnounceNowPlayingHandler(AbstractRequestHandler):
    def can_handle(self, handler_input: HandlerInput):
        return ask_utils.is_intent_name("AnnounceNowPlayingIntent")(handler_input)

    def handle(self, handler_input: HandlerInput):
        logger.info("In AnnounceNowPlayingHandler")

        metadata = player.Attributes.get_metadata_by_play_order(handler_input)
        if not metadata:
            return handler_input.response_builder.speak('Nothing is playing right now.').response
        return handler_input.response_builder.speak(f'This is {player.ssml_safe(metadata.title)} by {player.ssml_safe(metadata.artist)}').response
    
class CreatePlaylistHandler(AbstractRequestHandler):
    def can_handle(self, handler_input: HandlerInput) -> Response:
        return ask_utils.is_intent_name("CreatePlaylistIntent")(handler_input)
    
    def handle(self, handler_input: HandlerInput) -> Response:
        logger.info("In CreatePlaylistHandler")

        slots = handler_input.request_envelope.request.intent.slots
        playlist_id_encoded = slots['encodedPlaylistId'].value

        logger.info(f'playlist_id_encoded -> {playlist_id_encoded}')

        if not playlist_id_encoded or re.search(r'[^a-zA-Z0-9]', playlist_id_encoded): 
            return handler_input.response_builder.speak(f'Please provide encoded url in hexadecimal format.').response

        playlist_id = player.decode_hex(playlist_id_encoded.lower())
        if not playlist_id:
            return handler_input.response_builder.speak('That code does not look like valid hexadecimal. Please check it and try again.').response
        playlist, error = player.Api.get_playlist_info(handler_input, playlist_id)
        if error: return handler_input.response_builder.speak(str(error)).response
        playlist_name_original = playlist.title

        user_attr = player.Attributes.get_user_attributes(handler_input)
        if not user_attr.get('saved_playlists'): user_attr['saved_playlists'] = {}
        user_attr['saved_playlists'][playlist_name_original] = asdict(playlist)

        return handler_input.response_builder.speak(f'Playlist {player.ssml_safe(playlist_name_original)} saved.').response
    
class DeletePlaylistHandler(AbstractRequestHandler):
    def can_handle(self, handler_input: HandlerInput) -> Response:
        return ask_utils.is_intent_name("DeletePlaylistIntent")(handler_input)

    def handle(self, handler_input: HandlerInput) -> Response:
        logger.info("In DeletePlaylistHandler")

        slots = handler_input.request_envelope.request.intent.slots
        playlist_name = slots.get('playlistName').value

        if not playlist_name: return handler_input.response_builder.speak('Please provide playlist name.').response

        # fuzzy match on name to get key
        actual_playlist_name = player.Attributes.match_playlist_name(handler_input, playlist_name)
        if not actual_playlist_name:
            return handler_input.response_builder.speak(f'Could not find the playlist {player.ssml_safe(playlist_name)} in saved playlists.').response

        user_attr = player.Attributes.get_user_attributes(handler_input)
        user_attr.get('saved_playlists', {}).pop(actual_playlist_name, None)
        return handler_input.response_builder.speak(f'Playlist {player.ssml_safe(actual_playlist_name)} deleted.').response

class StartPlaylistHandler(AbstractRequestHandler):
    def can_handle(self, handler_input: HandlerInput) -> Response:
        return ask_utils.is_intent_name("StartPlaylistIntent")(handler_input)

    def handle(self, handler_input: HandlerInput) -> Response:
        logger.info("In StartPlaylistHandler")

        # player.send_progressive_response(handler_input, 'Searching...')
        slots = handler_input.request_envelope.request.intent.slots
        playlist_name = slots['playlistName'].value
        if not playlist_name: return handler_input.response_builder.speak('To play from saved playlists, say, "Alexa, ask DJ to play Favourites"').response
        
        # fuzzy match on name to get key
        actual_playlist_name = player.Attributes.match_playlist_name(handler_input, playlist_name)
        playlist = player.Attributes.get_from_saved_playlists(handler_input, actual_playlist_name)
        if not playlist: return handler_input.response_builder.speak(f'Could not find the playlist {player.ssml_safe(playlist_name)} in saved playlists.').response
        player.send_progressive_response(handler_input, f'Starting playlist {player.ssml_safe(playlist_name)}.')
        
        return player.Controller.fetch(handler_input, playlist_id=playlist.id)
    
class FindPlaylistHandler(AbstractRequestHandler):
    def can_handle(self, handler_input: HandlerInput) -> Response:
        return ask_utils.is_intent_name("FindPlaylistIntent")(handler_input)

    def handle(self, handler_input: HandlerInput) -> Response:
        logger.info("In FindPlaylistHandler")

        user_attr = player.Attributes.get_user_attributes(handler_input)
        saved_playlists = user_attr.get('saved_playlists')
        if saved_playlists: 
            to_string = ', '.join(list(saved_playlists.keys()))
            return handler_input.response_builder.speak(f'You have {player.ssml_safe(to_string)} in saved playlists.').response
        return handler_input.response_builder.speak(f'You do not have any playlists saved. To add playlists, say, "Alexa, ask DJ to add Playlist".').response
    
class SetApiurlHandler(AbstractRequestHandler):
    def can_handle(self, handler_input: HandlerInput) -> Response:
        return ask_utils.is_intent_name("SetApiurlIntent")(handler_input)

    def handle(self, handler_input: HandlerInput) -> Response:
        logger.info("In SetApiurlHandler")

        slots = handler_input.request_envelope.request.intent.slots
        api_url = slots['apiUrl'].value

        logger.info(f'api_url -> {api_url}')
        if not api_url or re.search(r'[^a-zA-Z0-9]', api_url): 
            return handler_input.response_builder.speak(f'Please provide encoded url in hexadeximal format.').response
        api_url_decoded = player.decode_hex(api_url.lower())
        if not api_url_decoded:
            return handler_input.response_builder.speak('That code does not look like valid hexadecimal. Please check it and try again.').response
        logger.info(f'api_url_decoded -> {api_url_decoded}')
        user_attr = player.Attributes.get_user_attributes(handler_input)
        user_attr['api_url'] = api_url_decoded
        return handler_input.response_builder.speak('Api url added.').response
# ###################################################################


# ########## Additional Helper HANDLERS #########################
# Contains some extra helpers

class HelpIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input: HandlerInput) -> Response:
        return ask_utils.is_intent_name("AMAZON.HelpIntent")(handler_input)

    def handle(self, handler_input: HandlerInput) -> Response:
        speak_output = "You can say 'Play Looks like me' or 'Ask DJ to play Looks like me'. You can say 'Play some jazz music' for a genre, 'Like this song' to save the current song, or 'Skip to 2 minutes 30 seconds' to jump within a song. You can add or delete playlists by saying 'Add playlist' or 'Delete playlist'. To access saved playlists, say 'Start playlist Favourites' or 'What are my playlists?' to find saved playlists."
        return (
            handler_input.response_builder
                .speak(speak_output)
                .ask(speak_output)
                .response
        )

class CancelOrStopIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input: HandlerInput) -> Response:
        return ask_utils.is_intent_name("AMAZON.CancelIntent")(handler_input)

    def handle(self, handler_input: HandlerInput) -> Response:
        speak_output = "Goodbye!"
        return (
            handler_input.response_builder
                .speak(speak_output)
                .response
        )

class FallbackIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input: HandlerInput) -> Response:
        # type: (HandlerInput) -> bool
        return ask_utils.is_intent_name("AMAZON.FallbackIntent")(handler_input)

    def handle(self, handler_input: HandlerInput) -> Response:
        # type: (HandlerInput) -> Response
        logger.info("In FallbackIntentHandler")
        speech = "Hmm, I'm not sure. Try asking for help?"

        return handler_input.response_builder.speak(speech).response

class SessionEndedRequestHandler(AbstractRequestHandler):
    def can_handle(self, handler_input: HandlerInput) -> Response:
        return ask_utils.is_request_type("SessionEndedRequest")(handler_input)

    def handle(self, handler_input: HandlerInput) -> Response:

        # Any cleanup logic goes here.

        return handler_input.response_builder.response


class IntentReflectorHandler(AbstractRequestHandler):
    def can_handle(self, handler_input: HandlerInput) -> Response:
        return ask_utils.is_request_type("IntentRequest")(handler_input)

    def handle(self, handler_input: HandlerInput) -> Response:
        intent_name = ask_utils.get_intent_name(handler_input)
        speak_output = "You just triggered " + intent_name + "."
        logger.info(speak_output)

        return (
            handler_input.response_builder
                .speak(speak_output)
                .response
        )

class CatchAllExceptionHandler(AbstractExceptionHandler):
    def can_handle(self, handler_input, exception):
        return True

    def handle(self, handler_input, exception):
        logger.error(exception, exc_info=True)

        # Speech is not allowed in responses to AudioPlayer / PlaybackController
        # events; an empty response is the only valid reply there.
        request_type = handler_input.request_envelope.request.object_type or ''
        if request_type.startswith(('AudioPlayer.', 'PlaybackController.')) or request_type == 'SessionEndedRequest':
            return handler_input.response_builder.response

        speak_output = "Sorry, I had trouble doing what you asked. Please try again."

        return (
            handler_input.response_builder
                .speak(speak_output)
                # .ask(speak_output)
                .response
        )
# ###################################################################

    
# ########## AUDIOPLAYER INTERFACE HANDLERS #########################
# This section contains handlers related to Audioplayer interface

class PlaybackStartedEventHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        # type: (HandlerInput) -> bool
        return ask_utils.is_request_type("AudioPlayer.PlaybackStarted")(handler_input)

    def handle(self, handler_input):
        # type: (HandlerInput) -> Response
        logger.info("In PlaybackStartedHandler")

        playback_info = player.Attributes.get_playback_info(handler_input)
        playback_info["index"] = player.Attributes.get_calculated_index(handler_input)
        playback_info["in_playback_session"] = True
        playback_info["has_previous_playback_session"] = True
        logger.info(f'playback_info -> {playback_info}')

        # Notify server: playback started (voice resume, auto-advance, etc.).
        # Include the offset so the web remote's progress bar can anchor its
        # local timer correctly — important when the track started at a non-zero
        # offset (a seek) or the app is opened partway through playback.
        token = player.Attributes.get_token(handler_input) or ''
        offset_in_ms = player.Attributes.get_offset_in_ms(handler_input) or 0
        player._notify_server(handler_input, 'started', video_id=token, offset_in_ms=offset_in_ms)

        # The seed track started fast (find_stream_list returned only it). Now
        # that audio is playing, lazily fill the radio/autoplay queue so tracks
        # 2+ are ready before this one nearly finishes. No-op if already filled.
        player.Controller.expand_radio_queue(handler_input)

        return handler_input.response_builder.response

class PlaybackFinishedEventHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        # type: (HandlerInput) -> bool
        return ask_utils.is_request_type("AudioPlayer.PlaybackFinished")(handler_input)

    def handle(self, handler_input):
        # type: (HandlerInput) -> Response
        logger.info("In PlaybackFinishedHandler")

        playback_info = player.Attributes.get_playback_info(handler_input)

        playback_info["in_playback_session"] = False
        playback_info["has_previous_playback_session"] = False
        playback_info["next_stream_enqueued"] = False

        # Notify server: playback finished (song ended naturally)
        player._notify_server(handler_input, 'finished')

        return handler_input.response_builder.response


class PlaybackStoppedEventHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        # type: (HandlerInput) -> bool
        return ask_utils.is_request_type("AudioPlayer.PlaybackStopped")(handler_input)

    def handle(self, handler_input):
        # type: (HandlerInput) -> Response
        logger.info("In PlaybackStoppedHandler")

        playback_info = player.Attributes.get_playback_info(handler_input)
        # playback_info["index"] = player.Attributes.get_index(handler_input)
        playback_info["offset_in_ms"] = player.Attributes.get_offset_in_ms(
            handler_input)

        # Notify server: playback stopped (voice pause, stop, or track change)
        player._notify_server(handler_input, 'stopped')

        return handler_input.response_builder.response


class PlaybackNearlyFinishedEventHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return ask_utils.is_request_type("AudioPlayer.PlaybackNearlyFinished")(handler_input)

    def handle(self, handler_input: HandlerInput):
        logger.info("In PlaybackNearlyFinishedHandler")

        playback_info = player.Attributes.get_playback_info(handler_input)
        playlist = player.Attributes.get_playlist(handler_input)
        playback_setting = player.Attributes.get_playback_setting(handler_input)

        if playback_info.get("next_stream_enqueued"):
            return handler_input.response_builder.response

        if not playlist:
            return handler_input.response_builder.response

        current_index = playback_info.get("index", 0)
        enqueue_index = (current_index + 1) % len(playlist)

        if enqueue_index == 0 and not playback_setting.get("loop"):
            # Reached the end of the stored window: page in the next batch of
            # the source playlist from the server (or radio continuation as a
            # fallback). This may trim long-played tracks, shifting the index.
            if not player.Controller.extend_queue(handler_input):
                return handler_input.response_builder.response
            playlist = player.Attributes.get_playlist(handler_input)
            current_index = playback_info.get("index", 0)
            enqueue_index = current_index + 1

        playback_info["next_stream_enqueued"] = True

        current_metadata = player.Attributes.get_metadata_by_play_order(handler_input, current_index)
        enqueue_metadata = player.Attributes.get_metadata_by_play_order(handler_input, enqueue_index) # playlist[enqueue_index]
        if not current_metadata or not enqueue_metadata:
            # Stale state; nothing sensible to enqueue. Speech is not allowed in
            # responses to AudioPlayer events, so end quietly.
            playback_info["next_stream_enqueued"] = False
            return handler_input.response_builder.response
        current_video_id = current_metadata.video_id
        enqueue_video_id = enqueue_metadata.video_id
        enqueue_stream, error = player.Api.get_stream(handler_input, enqueue_video_id)
        if error:
            logger.error(f'Could not enqueue next stream: {error}')
            playback_info["next_stream_enqueued"] = False
            return handler_input.response_builder.response

        # Log all attrubutes ----------------------------------
        player.Attributes.log_attributes(handler_input)
        # -----------------------------------------------------

        handler_input.response_builder.add_directive(
            PlayDirective(
                play_behavior=PlayBehavior.ENQUEUE,
                audio_item=AudioItem(
                    stream=Stream(
                        token=enqueue_video_id,
                        url=enqueue_stream.audio_url,
                        offset_in_milliseconds=0,
                        expected_previous_token=current_video_id),
                    metadata=player.Attributes.get_audio_item_metadata(enqueue_metadata))))
        
        logger.info(f'current_video_id -> {current_video_id}, enqueue_video_id -> {enqueue_video_id}, current_index -> {current_index}, enqueue_index -> {enqueue_index}')


        return handler_input.response_builder.response


def _without_speech(response: Response) -> Response:
    # Responses to PlaybackController events must not carry speech; strip any
    # error message the shared Controller paths may have attached.
    response.output_speech = None
    response.reprompt = None
    return response

class PlayCommandHandler(AbstractRequestHandler):
    """Play/resume button on the Alexa app's now-playing card or a remote."""
    def can_handle(self, handler_input):
        return ask_utils.is_request_type("PlaybackController.PlayCommandIssued")(handler_input)

    def handle(self, handler_input: HandlerInput):
        logger.info("In PlayCommandHandler")
        return _without_speech(player.Controller.resume(handler_input, is_playback=True))

class PauseCommandHandler(AbstractRequestHandler):
    """Pause button on the Alexa app's now-playing card or a remote."""
    def can_handle(self, handler_input):
        return ask_utils.is_request_type("PlaybackController.PauseCommandIssued")(handler_input)

    def handle(self, handler_input: HandlerInput):
        logger.info("In PauseCommandHandler")
        return _without_speech(player.Controller.pause(handler_input))

class NextCommandHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return ask_utils.is_request_type("PlaybackController.NextCommandIssued")(handler_input)

    def handle(self, handler_input: HandlerInput):
        logger.info("In NextCommandHandler")
        return _without_speech(player.Controller.play_next(handler_input, is_playback=True))

class PreviousCommandHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return ask_utils.is_request_type("PlaybackController.PreviousCommandIssued")(handler_input)

    def handle(self, handler_input: HandlerInput):
        logger.info("In PreviousCommandHandler")
        return _without_speech(player.Controller.play_previous(handler_input, is_playback=True))


class PlaybackFailedEventHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        # type: (HandlerInput) -> bool
        return ask_utils.is_request_type("AudioPlayer.PlaybackFailed")(handler_input)

    def handle(self, handler_input):
        # type: (HandlerInput) -> Response
        logger.info("In PlaybackFailedHandler")

        playback_info = player.Attributes.get_playback_info(handler_input)

        logger.info("Playback Failed: {}".format(
            handler_input.request_envelope.request.error))

        playback_info["in_playback_session"] = False
        playback_info["next_stream_enqueued"] = False
        # A failed current track is terminal. Clear Alexa's AudioPlayer queue so
        # an already-enqueued next item cannot start automatically, and leave
        # the failed item selected for an explicit user retry or selection.
        player._notify_server(handler_input, 'stopped')
        return _without_speech(player.Controller.stop(handler_input))

# ###################################################################
    

# ############# REQUEST / RESPONSE INTERCEPTORS #####################
class LogRequestInterceptor(AbstractRequestInterceptor):
    def process(self, handler_input: HandlerInput):
        logger.info(f"Request type: {handler_input.request_envelope.request.object_type}")

class LoadPersistenceAttributesRequestInterceptor(AbstractRequestInterceptor):
    def process(self, handler_input: HandlerInput):
        persistence_attr = handler_input.attributes_manager.persistent_attributes

        user_id = player.Attributes.get_user_id(handler_input)

        if not persistence_attr.get(user_id):
            persistence_attr[user_id] = {
                'playback_setting': {
                    "loop": False,
                    "shuffle": False
                },
                'playback_info': {
                    "play_order": [],
                    "index": 0,
                    "offset_in_ms": 0,
                    "next_stream_enqueued": False,
                    "in_playback_session": False,
                    "has_previous_playback_session": False,
                    "stream_url": None
                },
                'playlist': [],
                'saved_playlists': {},
                'api_url': None
            }

        else:
            # Convert decimals to integers, because of AWS SDK DynamoDB issue
            # https://github.com/boto/boto3/issues/369
            
            playback_info = player.Attributes.get_user_attributes(handler_input).get("playback_info")
            playback_info["index"] = int(playback_info.get("index", 0))
            playback_info["play_order"] = [int(i) for i in playback_info.get("play_order", [])]
            playback_info["offset_in_ms"] = int(playback_info.get("offset_in_ms", 0))

            playlist = player.Attributes.get_user_attributes(handler_input).get("playlist") or []
            for metadata in playlist:
                thumbnail = metadata.get('thumbnail')
                if not thumbnail: continue
                thumbnail['width'] = int(thumbnail['width'])  # Convert Decimal to int
                thumbnail['height'] = int(thumbnail['height'])  # Convert Decimal to int

            # Heal oversized legacy state: playlists persisted before the
            # sliding window could hold hundreds of tracks, pushing the item
            # toward DynamoDB's 400KB cap where every save fails. (No-op when
            # already within bounds; never raises.)
            player.Controller.trim_window(handler_input)

class SavePersistenceAttributesResponseInterceptor(AbstractResponseInterceptor):
    def process(self, handler_input: HandlerInput, response):
        handler_input.attributes_manager.save_persistent_attributes()
# ###################################################################


sb.add_request_handler(LaunchRequestHandler())

sb.add_request_handler(PlayArtistIntentHandler())
sb.add_request_handler(PlayAlbumIntentHandler())

sb.add_request_handler(PlaySongIntentHandler())
sb.add_request_handler(StartPlaybackHandler())
sb.add_request_handler(PausePlaybackHandler())
sb.add_request_handler(StopPlaybackHandler())
sb.add_request_handler(NextPlaybackHandler())
sb.add_request_handler(PreviousPlaybackHandler())
sb.add_request_handler(SeekIntentHandler())
sb.add_request_handler(FastForwardIntentHandler())
sb.add_request_handler(RewindIntentHandler())
sb.add_request_handler(LikeSongIntentHandler())
sb.add_request_handler(PlayGenreIntentHandler())
sb.add_request_handler(LoopOnHandler())
sb.add_request_handler(LoopOffHandler())
sb.add_request_handler(ShuffleOnHandler())
sb.add_request_handler(ShuffleOffHandler())
sb.add_request_handler(StartOverHandler())
sb.add_request_handler(AnnounceNowPlayingHandler())
sb.add_request_handler(CreatePlaylistHandler())
sb.add_request_handler(DeletePlaylistHandler())
sb.add_request_handler(StartPlaylistHandler())
sb.add_request_handler(FindPlaylistHandler())
sb.add_request_handler(SetApiurlHandler())

# More handlers
sb.add_request_handler(HelpIntentHandler())
sb.add_request_handler(CancelOrStopIntentHandler())
sb.add_request_handler(FallbackIntentHandler())
sb.add_request_handler(SessionEndedRequestHandler())
sb.add_request_handler(IntentReflectorHandler())

# Interface handlers
sb.add_request_handler(PlaybackStartedEventHandler())
sb.add_request_handler(PlaybackFinishedEventHandler())
sb.add_request_handler(PlaybackStoppedEventHandler())
sb.add_request_handler(PlaybackNearlyFinishedEventHandler())
sb.add_request_handler(PlaybackFailedEventHandler())

# PlaybackController events (buttons in the Alexa app / on remotes)
sb.add_request_handler(PlayCommandHandler())
sb.add_request_handler(PauseCommandHandler())
sb.add_request_handler(NextCommandHandler())
sb.add_request_handler(PreviousCommandHandler())

# Exceptions
sb.add_global_request_interceptor(LogRequestInterceptor())
sb.add_exception_handler(CatchAllExceptionHandler())

# Interceptors
sb.add_global_request_interceptor(LoadPersistenceAttributesRequestInterceptor())
sb.add_global_response_interceptor(SavePersistenceAttributesResponseInterceptor())

lambda_handler = sb.lambda_handler()
