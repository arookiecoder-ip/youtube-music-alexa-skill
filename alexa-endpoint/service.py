"""Persistent ASK compute service. Only the authenticated Lambda relay dispatches."""
import datetime as dt
import hashlib
import hmac
import json
import logging
import os
import re
import threading
import time

from flask import Flask, jsonify, request
from runtime_relay import signature, state_key, _needs_state


class ActorLocks:
    def __init__(self, capacity=256):
        self.guard, self.entries, self.capacity = threading.Lock(), {}, capacity

    def acquire(self, actor, timeout=1):
        with self.guard:
            if actor not in self.entries:
                if len(self.entries) >= self.capacity:
                    return None
                self.entries[actor] = [threading.Lock(), 0]
            entry = self.entries[actor]
            entry[1] += 1
        if not entry[0].acquire(timeout=timeout):
            self.release(actor, acquired=False)
            return None
        return actor

    def release(self, actor, acquired=True):
        with self.guard:
            entry = self.entries[actor]
            if acquired:
                entry[0].release()
            entry[1] -= 1
            if not entry[1]:
                del self.entries[actor]


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate JSON key')
            result[key] = value
        return result
    value = json.loads(raw.decode('utf-8'), object_pairs_hook=pairs,
                       parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite JSON')))
    if not isinstance(value, dict):
        raise ValueError('JSON object required')
    return value


def create_app(adapter, handler, ledger, *, secret, account_id, skill_id):
    if not isinstance(secret, str) or len(secret) < 43 or not account_id or not skill_id:
        raise ValueError('private runtime configuration required')
    app = Flask(__name__)
    app.config.update(MAX_CONTENT_LENGTH=1024 * 1024)
    locks = ActorLocks()
    logger = logging.getLogger(__name__)

    @app.get('/health/ready')
    def ready():
        return jsonify({'ready': True})

    @app.post('/alexa/skill/relay/')
    def relay():
        started = time.monotonic()
        stamp = request.headers.get('X-MusicBox-Relay-Time', '')
        nonce = request.headers.get('X-MusicBox-Relay-Nonce', '')
        supplied = request.headers.get('X-MusicBox-Relay-Signature', '')
        if (not re.fullmatch(r'[0-9]{10,11}', stamp) or abs(time.time() - int(stamp)) > 30
                or not re.fullmatch(r'[0-9a-f]{32}', nonce) or not re.fullmatch(r'[0-9a-f]{64}', supplied)):
            return '', 403
        raw = request.get_data()
        if not hmac.compare_digest(signature(secret, stamp, nonce, raw), supplied):
            return '', 403
        try:
            payload = strict_json(raw)
            event = payload['event']
            system, req = event['context']['System'], event['request']
            if (payload['account_id'] != account_id
                    or system['application']['applicationId'] != skill_id
                    or event.get('session', {}).get('application', {}).get('applicationId', skill_id) != skill_id):
                return '', 403
            request_id = req['requestId']
            actor = (system['user']['userId'], system['device']['deviceId'])
            if not isinstance(request_id, str) or not 1 <= len(request_id) <= 512:
                raise ValueError('request ID')
            if not all(isinstance(v, str) and 1 <= len(v) <= 1024 for v in actor):
                raise ValueError('actor')
            stamp_event = req['timestamp']
            if not isinstance(stamp_event, str) or not stamp_event.endswith('Z'):
                raise ValueError('timestamp')
            moment = dt.datetime.fromisoformat(stamp_event[:-1] + '+00:00')
            if abs((dt.datetime.now(dt.timezone.utc) - moment).total_seconds()) > 150:
                raise ValueError('timestamp')
            identity, key = payload.get('identity'), payload.get('key')
            if _needs_state(event):
                if not isinstance(identity, dict) or key != state_key(event, identity.get('binding')):
                    return '', 403
            elif key is not None or identity is not None:
                return '', 403
            if payload['params'] != __import__('skill_bootstrap')._params(event):
                raise ValueError('authorization parameters')
            attrs = payload['attributes']
            if not isinstance(attrs, dict) or set(attrs) != {'M'} or len(json.dumps(attrs).encode()) > 400 * 1024:
                raise ValueError('persistence bounds')
            from boto3.dynamodb.types import TypeDeserializer
            TypeDeserializer().deserialize(attrs)
        except (KeyError, ValueError, TypeError, AttributeError, OverflowError, RecursionError):
            return '', 400
        acquired = locks.acquire(actor)
        if acquired is None:
            return '', 503, {'Retry-After': '1'}
        try:
            if not ledger.claim(request_id, hashlib.sha256(raw).hexdigest(), time.time()):
                result = {'response': {'version': '1.0', 'response': {'shouldEndSession': True}}, 'dirty': False}
            else:
                import skill_bootstrap
                import urllib3
                if identity is not None:
                    skill_bootstrap.install_response(event, urllib3.HTTPResponse(status=200, body=json.dumps(identity).encode()))
                try:
                    with adapter.request(key, attrs) as state:
                        response = handler(event, None)
                        result = {'response': response, 'key': key, 'dirty': state['dirty']}
                        if state['dirty']:
                            result['attributes'] = state['attributes']
                finally:
                    skill_bootstrap.clear()
            encoded = json.dumps(result, separators=(',', ':')).encode()
            response = app.response_class(encoded, content_type='application/json')
            response.headers['X-MusicBox-Relay-Signature'] = signature(secret, stamp, nonce, encoded, response=True)
            logger.info('Persistent Alexa dispatch_ms=%.0f', (time.monotonic() - started) * 1000)
            return response
        except ValueError:
            return '', 400
        except Exception as exc:
            logger.error('Persistent Alexa dispatch failed: %s', type(exc).__name__)
            return '', 503
        finally:
            locks.release(acquired)

    @app.after_request
    def headers(response):
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response

    return app


def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(name)s %(levelname)s %(message)s')
    os.environ['MUSICBOX_PERSISTENCE_MODE'] = 'relay'
    from skill_runtime import dynamodb_adapter, lambda_handler
    from relay_store import ReplayLedger
    from waitress import serve
    import data
    import skill_bootstrap
    skill_bootstrap.runtime_ready(0)
    for name in ('mediaUtils.player', 'skill_runtime', 'urllib3', 'botocore'):
        logging.getLogger(name).setLevel(logging.WARNING)
    ledger = ReplayLedger(os.environ.get('MUSICBOX_SKILL_DB', '/state/requests.db'),
                          data.MUSICBOX_ACCOUNT_ID, os.environ['MUSICBOX_SKILL_ID'])
    app = create_app(dynamodb_adapter, lambda_handler, ledger,
                     secret=data.MUSICBOX_RUNTIME_SECRET, account_id=data.MUSICBOX_ACCOUNT_ID,
                     skill_id=os.environ['MUSICBOX_SKILL_ID'])
    serve(app, host='0.0.0.0', port=8000, threads=8, connection_limit=64,
          max_request_body_size=1024 * 1024, max_request_header_size=16384,
          channel_timeout=15, ident='MusicBox')


if __name__ == '__main__':
    main()
