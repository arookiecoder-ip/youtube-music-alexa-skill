# Fallback API URL used when none has been set via the "set api url" voice command.
# Shared secret; must match the API_KEY env var on the Flask server.
# Both live in api_key.py (gitignored) so the repo can be public.
try:
    from api_key import API_KEY, DEFAULT_API_URL
except ImportError:
    import logging
    logging.warning("api_key.py not found; API_KEY and DEFAULT_API_URL are empty")
    API_KEY = ""
    DEFAULT_API_URL = ""

WELCOME_MSG = "Welcome to music box. You can say, play blinding lights, to begin."
WELCOME_REPROMPT_MSG = "You can say, play, followed by a song name, to begin."
WELCOME_PLAYBACK_MSG = "You were listening to {}. Would you like to resume?"
WELCOME_PLAYBACK_REPROMPT_MSG = "You can say yes to resume or no to play from the top"
DEVICE_NOT_SUPPORTED = "Sorry, this skill is not supported on this device"
LOOP_ON_MSG = "Loop turned on."
LOOP_OFF_MSG = "Loop turned off."
HELP_MSG = WELCOME_MSG
HELP_PLAYBACK_MSG = WELCOME_PLAYBACK_MSG
HELP_DURING_PLAY_MSG = "You are listening to music box. You can say, Next or Previous to navigate through the playlist. At any time, you can say Pause to pause the audio and Resume to resume."
STOP_MSG = "Goodbye."
EXCEPTION_MSG = "Sorry, this is not a valid command. Please say help, to hear what you can say."
PLAYBACK_PLAY = "Playing {} by {}"
PLAYBACK_PLAY_CARD = "Playing {}"
PLAYBACK_NEXT_END = "You have reached the end of the playlist"
PLAYBACK_PREVIOUS_END = "You have reached the start of the playlist"
API_CONNECTION_ISSUE = 'Could not connect to api url. Please check the connection to the url or set a new url.'
API_URL_NOT_SET = 'Api url not set. To set api url, say, "Alexa, ask DJ to set api url"'
NOT_FOUND = "Sorry, I couldn't find that. Please try something else."
SERVICE_ISSUE = 'Sorry, the music service had a problem. Please try again in a moment.'
NOTHING_TO_RESUME = 'There is nothing to resume. Say, play, followed by a song name, to begin.'