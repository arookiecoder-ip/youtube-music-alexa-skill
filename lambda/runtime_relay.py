"""Small authenticated Lambda relay. DynamoDB credentials stay inside Lambda."""
import hashlib
import hmac
import json
import logging
import os
import re
import time
import uuid
from urllib.parse import urlsplit

import data
import skill_bootstrap

_client = None
logger = logging.getLogger(__name__)
QUIET = {'version': '1.0', 'response': {'shouldEndSession': True}}


def _needs_state(event):
    request = event['request']
    return (request.get('type') not in ('System.ExceptionEncountered', 'SessionEndedRequest')
            and (request.get('intent') or {}).get('name') not in ('LinkAccountIntent', 'SetApiurlIntent'))


def state_key(event, binding):
    system = event['context']['System']
    values = (system['application']['applicationId'], system['user']['userId'],
              system['device']['deviceId'], binding)
    if not all(isinstance(v, str) and v for v in values) or not re.fullmatch('[0-9a-f]{64}', binding):
        raise ValueError('invalid persistence identity')
    return 'echo-v2:' + hashlib.sha256('\0'.join(values).encode()).hexdigest()


def _ddb():
    global _client
    if _client is None:
        import boto3
        from botocore.config import Config
        _client = boto3.client('dynamodb', region_name=os.environ['DYNAMODB_PERSISTENCE_REGION'],
            config=Config(connect_timeout=1, read_timeout=1, retries={'max_attempts': 0}))
    return _client


def signature(secret, timestamp, nonce, raw, *, response=False):
    prefix = b'response\n' if response else b'request\n'
    message = prefix + timestamp.encode() + b'\n' + nonce.encode() + b'\n' + raw
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def invoke(event, context):
    import urllib3
    started = time.monotonic()
    secret = getattr(data, 'MUSICBOX_RUNTIME_SECRET', '')
    if not isinstance(secret, str) or len(secret) < 43:
        raise ValueError('runtime relay secret is not configured')
    url = skill_bootstrap._api_url()
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or parsed.path not in ('', '/'):
        raise ValueError('runtime relay requires the configured HTTPS backend origin')
    identity, key, item = None, None, {}
    params = skill_bootstrap._params(event)
    if _needs_state(event):
        response = skill_bootstrap._fetch(url, params, skill_bootstrap.identity_headers(event))
        if response.status != 200:
            # Existing SDK authorization handler formats denied access. No state
            # is read or relayed on this path, and no command was authorized.
            raise RelayAuthorizationDenied(response)
        identity = json.loads(response.data)
        key = state_key(event, identity.get('binding'))
        item = _ddb().get_item(TableName=os.environ['DYNAMODB_PERSISTENCE_TABLE_NAME'],
                               Key={'id': {'S': key}}, ConsistentRead=True).get('Item', {})
    payload = {'event': event, 'account_id': data.MUSICBOX_ACCOUNT_ID,
               'identity': identity, 'params': params, 'key': key,
               'attributes': item.get('attributes', {'M': {}})}
    raw = json.dumps(payload, separators=(',', ':')).encode()
    if len(raw) > 1024 * 1024:
        raise ValueError('runtime relay payload exceeds bounds')
    stamp, nonce = str(int(time.time())), uuid.uuid4().hex
    remaining = getattr(context, 'get_remaining_time_in_millis', lambda: 6500)() / 1000
    budget = min(4, remaining - 1.5)
    if budget <= 0:
        raise TimeoutError('relay invocation budget exhausted')
    response = skill_bootstrap.http_client().request('POST', url + '/alexa/skill/relay/', body=raw,
        headers={'Content-Type': 'application/json', 'X-MusicBox-Relay-Time': stamp,
                 'X-MusicBox-Relay-Nonce': nonce,
                 'X-MusicBox-Relay-Signature': signature(secret, stamp, nonce, raw)},
        timeout=urllib3.Timeout(total=budget), retries=False, redirect=False)
    if response.status != 200 or len(response.data) > 1024 * 1024:
        raise RuntimeError('persistent runtime unavailable')
    expected = signature(secret, stamp, nonce, response.data, response=True)
    supplied = response.headers.get('X-MusicBox-Relay-Signature', '')
    if not hmac.compare_digest(expected.encode(), supplied.encode()):
        raise ValueError('runtime response signature')
    result = json.loads(response.data)
    if result.get('dirty'):
        if key is None or result.get('key') != key:
            raise ValueError('runtime persistence scope')
        value = result['attributes']
        if set(value) != {'M'} or len(json.dumps(value).encode()) > 400 * 1024:
            raise ValueError('runtime persistence size')
        old_revision = item.get('musicbox_runtime_revision')
        updated = dict(item, id={'S': key}, attributes=value, musicbox_runtime_revision={'S': nonce})
        kwargs = {'TableName': os.environ['DYNAMODB_PERSISTENCE_TABLE_NAME'], 'Item': updated,
                  'ExpressionAttributeNames': {'#r': 'musicbox_runtime_revision'}}
        if old_revision is None:
            kwargs['ConditionExpression'] = 'attribute_not_exists(#r)'
        else:
            kwargs.update(ConditionExpression='#r = :previous', ExpressionAttributeValues={':previous': old_revision})
        # Do not return an AudioPlayer directive unless its state is durable.
        # A concurrent stale whole-item write is rejected rather than clobbering.
        _ddb().put_item(**kwargs)
    logger.info('[skill-relay] request_ms=%.0f sdk_loaded=False', (time.monotonic() - started) * 1000)
    return result['response']


class RelayAuthorizationDenied(Exception):
    def __init__(self, response):
        self.response = response
