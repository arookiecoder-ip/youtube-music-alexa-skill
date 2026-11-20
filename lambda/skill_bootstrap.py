"""Small request-local authorization prefetch; no SDK or persistence imports."""
import re
import threading
from urllib.parse import urlencode, urlsplit
import data

_client = None
_client_lock = threading.Lock()
_pending = threading.local()
_runtime_load_ms = None


def http_client():
    global _client
    with _client_lock:
        if _client is None:
            import urllib3
            _client = urllib3.PoolManager()
        return _client


def identity_params(kind, intent='', query=''):
    if kind == 'LaunchRequest':
        return {'include_control': '1'}
    if kind == 'PlaybackController.PlayCommandIssued' or (
            kind == 'IntentRequest' and intent in ('AMAZON.ResumeIntent', 'PlayAudio')):
        return {'include_resume': '1'}
    if kind == 'IntentRequest' and intent == 'PlaySongIntent' and isinstance(query, str):
        query = re.sub(r'^\s*silentmode\b[\s,]*', '', query, flags=re.IGNORECASE)
        if re.fullmatch(r'\s*(?:the\s+)?app\s+selection\s*', query, flags=re.IGNORECASE):
            return {'include_play': '1'}
    return {}


def _system(event):
    return event.get('context', {}).get('System', {})


def _params(event):
    request = event.get('request', {})
    intent = request.get('intent') or {}
    query = ((intent.get('slots') or {}).get('songName') or {}).get('value', '')
    return identity_params(request.get('type'), intent.get('name'), query)


def _api_url():
    url = data.DEFAULT_API_URL
    parsed = urlsplit(url)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username
            or parsed.password or parsed.query or parsed.fragment
            or any(char.isspace() or ord(char) < 32 for char in url)):
        raise ValueError('Invalid configured backend URL')
    parsed.port
    return url.rstrip('/')


def can_prefetch(event):
    try:
        request, system = event['request'], _system(event)
        if request.get('type') in ('System.ExceptionEncountered', 'SessionEndedRequest'):
            return False
        intent = (request.get('intent') or {}).get('name')
        if intent in ('LinkAccountIntent', 'SetApiurlIntent'):
            return False
        _api_url()
        values = (request.get('requestId'), system.get('user', {}).get('userId'),
                  system.get('device', {}).get('deviceId'), system.get('apiAccessToken'),
                  system.get('apiEndpoint'), getattr(data, 'MUSICBOX_ALEXA_TOKEN', ''))
        return all(isinstance(value, str) and value for value in values)
    except (AttributeError, KeyError, TypeError, ValueError):
        return False


def identity_headers(event):
    system, request = _system(event), event['request']
    account_id = getattr(data, 'MUSICBOX_ACCOUNT_ID', '')
    headers = {
        'Authorization': 'Bearer ' + data.MUSICBOX_ALEXA_TOKEN,
        **({'X-MusicBox-Account-Id': account_id} if account_id else
           {'X-MusicBox-Email': getattr(data, 'MUSICBOX_EMAIL', '')}),
        'X-Alexa-User-Id': system.get('user', {}).get('userId', ''),
        'X-Alexa-Device-Id': system.get('device', {}).get('deviceId', ''),
        'X-Alexa-Profile-Token': system.get('apiAccessToken', ''),
        'X-Alexa-Api-Endpoint': system.get('apiEndpoint', ''),
        'X-Alexa-Request-Id': request.get('requestId', ''),
        'X-Alexa-Request-Type': request.get('type', ''),
    }
    headers.update(control_headers(system.get('application', {}).get('applicationId', '')))
    intent = (request.get('intent') or {}).get('name')
    if intent:
        headers['X-Alexa-Intent-Name'] = intent
    token = request.get('token')
    if not token and request.get('type', '').startswith('PlaybackController.'):
        token = event.get('context', {}).get('AudioPlayer', {}).get('token')
    if token:
        headers['X-Alexa-Playback-Token'] = token
    current = (request.get('currentPlaybackState') or {}).get('token')
    if current:
        headers['X-Alexa-Current-Playback-Token'] = current
    if request.get('timestamp'):
        headers['X-Alexa-Request-Timestamp'] = request['timestamp']
    return headers


def control_headers(application_id):
    if isinstance(application_id, str) and re.fullmatch(
            r'amzn1\.ask\.skill\.[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}', application_id):
        return {'X-MusicBox-Control-Launch': '1',
                'X-MusicBox-Application-Id': application_id}
    return {}


def _fetch(url, params, headers):
    import urllib3
    return http_client().request('GET', url + '/api/alexa/identity/?' + urlencode(params),
        headers={**headers, 'X-Skill-Auth-Overlap': '1'},
        timeout=urllib3.Timeout(total=3.0), retries=False, redirect=False)


def start(event, executor):
    clear()
    url, params = _api_url(), _params(event)
    future = executor.submit(_fetch, url, params, identity_headers(event))
    _pending.item = (event['request']['requestId'], url, params, future)


def take(request_id, url, params):
    item = getattr(_pending, 'item', None)
    clear()
    if item is not None and item[:3] == (request_id, url, params):
        return item[3]
    return None


def clear():
    if hasattr(_pending, 'item'):
        del _pending.item


def runtime_ready(milliseconds):
    global _runtime_load_ms
    _runtime_load_ms = milliseconds


def runtime_headers():
    if _runtime_load_ms is None:
        return {}
    return {'X-Skill-SDK-Load-Ms': format(_runtime_load_ms, '.0f')}
