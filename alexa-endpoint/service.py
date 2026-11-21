"""Always-running Alexa endpoint. No request is dispatched before verification."""
import hashlib
import hmac
import logging
import os
import threading
import time

from flask import Flask, jsonify, request
from verification import VerificationError, Verifier


class ActorLocks:
    def __init__(self, capacity=256):
        self.guard = threading.Lock()
        self.entries = {}
        self.capacity = capacity

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


def create_app(adapter, handler, verifier, *, migration_token='', migration_expires=0, transfer_id=''):
    app = Flask(__name__)
    app.config.update(MAX_CONTENT_LENGTH=16 * 1024 * 1024)
    locks = ActorLocks()
    quiet = {'version': '1.0', 'response': {'shouldEndSession': True}}

    @app.get('/health/ready')
    def ready():
        return jsonify({'ready': adapter.ready()}), 200 if adapter.ready() else 503

    @app.route('/alexa/skill/migration/', methods=['GET', 'POST'])
    def migration():
        supplied = request.headers.get('X-MusicBox-Migration', '')
        if (not migration_token or time.time() >= migration_expires
                or not hmac.compare_digest(supplied.encode(), migration_token.encode())):
            return '', 404
        try:
            if request.method == 'GET':
                return jsonify({'account_id': adapter.account_id, 'skill_id': adapter.skill_id,
                                'transfer_id': transfer_id, 'items': adapter.export_snapshot()})
            from verification import strict_json
            payload = strict_json(request.get_data())
            if (payload['account_id'] != adapter.account_id or payload['skill_id'] != adapter.skill_id
                    or payload['transfer_id'] != transfer_id):
                return '', 403
            if payload.get('phase') in ('started', 'failed'):
                code = payload.get('code', '')
                if code not in ('', 'AccessDeniedException', 'ValidationException', 'RuntimeError',
                                'ClientError', 'TypeError', 'ValueError', 'KeyError'):
                    code = 'other'
                logging.getLogger(__name__).warning('Skill migration phase=%s code=%s', payload['phase'], code)
                return jsonify({'ok': True})
            digest = adapter.import_snapshot(payload['items'], transfer_id)
            return jsonify({'ok': True, 'digest': digest, 'count': len(payload['items'])})
        except (KeyError, TypeError, ValueError):
            return '', 400

    @app.post('/alexa/skill/')
    def skill():
        started = time.monotonic()
        if request.content_length is not None and request.content_length > 512 * 1024:
            return '', 413
        raw = request.get_data()
        try:
            event = verifier.verify(raw, request.headers)
        except VerificationError:
            return '', 400
        if not adapter.ready():
            return '', 503, {'Retry-After': '2'}
        actor = event['context']['System']
        actor_key = (actor['user']['userId'], actor['device']['deviceId'])
        acquired = locks.acquire(actor_key)
        if acquired is None:
            return '', 503, {'Retry-After': '1'}
        try:
            if not adapter.claim_request(event['request']['requestId'], hashlib.sha256(raw).hexdigest(), time.time()):
                return jsonify(quiet)
            response = handler(event, None)
            logging.getLogger(__name__).info('Alexa verified request type=%s dispatch_ms=%.0f',
                str(event['request'].get('type', ''))[:80].replace('\n', ''),
                (time.monotonic() - started) * 1000)
            return jsonify(response)
        except ValueError:
            return '', 400
        except Exception:
            # Deliberately omit exception strings: SDK errors can contain URLs/tokens.
            logging.getLogger(__name__).error('Alexa dispatch failed')
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
    # Private configuration is loaded before handler modules; never print it.
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(name)s %(levelname)s %(message)s')
    os.environ['MUSICBOX_PERSISTENCE_MODE'] = 'sqlite'
    from skill_runtime import dynamodb_adapter, lambda_handler
    from waitress import serve
    for name in ('mediaUtils.player', 'skill_runtime', 'urllib3', 'botocore'):
        logging.getLogger(name).setLevel(logging.WARNING)
    app = create_app(dynamodb_adapter, lambda_handler,
                     Verifier(os.environ['MUSICBOX_SKILL_ID']),
                     migration_token=os.environ.get('MUSICBOX_MIGRATION_TOKEN', ''),
                     migration_expires=float(os.environ.get('MUSICBOX_MIGRATION_EXPIRES', '0')),
                     transfer_id=os.environ.get('MUSICBOX_MIGRATION_ID', ''))
    serve(app, host='0.0.0.0', port=8000, threads=8, connection_limit=64,
          max_request_body_size=16 * 1024 * 1024, channel_timeout=15,
          ident='MusicBox')


if __name__ == '__main__':
    main()
