"""Private-skill configuration. No server-wide key or skill ID is distributed."""
import os
from pathlib import Path
import shlex

_CONFIG_NAMES = frozenset(('MUSICBOX_SERVER_URL', 'MUSICBOX_EMAIL', 'MUSICBOX_ALEXA_TOKEN'))


def _read_private_env():
    """Read only this Lambda's .env; never search parent folders or expand values."""
    try:
        lines = Path(__file__).with_name('.env').read_text(encoding='utf-8-sig').splitlines()
    except FileNotFoundError:
        return {}
    values = {}
    for number, line in enumerate(lines, 1):
        line = line.strip()
        if line.startswith('export '):
            line = line[7:].lstrip()
        name, separator, value = line.partition('=')
        name = name.strip()
        if name not in _CONFIG_NAMES:
            continue
        try:
            parts = shlex.split(value, comments=True, posix=True)
        except ValueError:
            raise RuntimeError('Invalid private .env setting on line %s.' % number) from None
        if not separator or len(parts) > 1:
            raise RuntimeError('Invalid private .env setting on line %s.' % number)
        values[name] = parts[0] if parts else ''
    return values


_file_config = _read_private_env()


def _setting(name):
    # An explicitly empty environment value also overrides the file.
    return os.environ.get(name, _file_config.get(name, ''))


MUSICBOX_SERVER_URL = _setting('MUSICBOX_SERVER_URL')
MUSICBOX_EMAIL = _setting('MUSICBOX_EMAIL')
MUSICBOX_ALEXA_TOKEN = _setting('MUSICBOX_ALEXA_TOKEN')
DEFAULT_API_URL = MUSICBOX_SERVER_URL
# All deployments use scoped credentials and isolated persistence.
ALEXA_SKILL_NAMESPACE = 'private-account'

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
API_CONNECTION_ISSUE = 'Could not connect to the music server. Please try again in a moment.'
API_URL_NOT_SET = 'The skill administrator needs to configure the music server address.'
NOT_FOUND = "Sorry, I couldn't find that. Please try something else."
SERVICE_ISSUE = 'Sorry, the music service had a problem. Please try again in a moment.'
NOTHING_TO_RESUME = 'There is nothing to resume. Say, play, followed by a song name, to begin.'
