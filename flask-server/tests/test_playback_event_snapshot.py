"""Execute the production route with isolated auth and playback dependencies."""
import ast
from pathlib import Path
import threading
from unittest.mock import Mock
from flask import Flask, request, jsonify
from playback_events import PlaybackEvents


def setup(logged_in=True):
    app = Flask(__name__)
    state = dict(queue=[{'video_id': 'same-song', 'entry_id': 'first'},
                        {'video_id': 'same-song', 'entry_id': 'second'}],
                 queue_version=10, output_token='owner-one')
    namespace = dict(app=app, request=request, jsonify=jsonify,
        _logged_in=Mock(return_value=logged_in), _effective_serial=lambda value: value,
        _np_lock=threading.Lock(), _np_snapshot=lambda *args, **kwargs: dict(state),
        playback_events=PlaybackEvents())
    path = Path(__file__).resolve().parents[1] / 'server.py'
    function = next(node for node in ast.parse(path.read_text()).body
        if isinstance(node, ast.FunctionDef) and node.name == 'playback_event_snapshot')
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), 'exec'), namespace)
    return app.test_client(), state


def test_owner_only_even_if_other_auth_middleware_would_allow_a_key_or_guest():
    client, _ = setup(False)
    assert client.get('/api/app/events/snapshot/', headers={'X-Api-Key': 'valid-owner-key'}).status_code == 401


def test_only_unchanged_queue_and_output_token_can_omit_entries():
    client, state = setup()
    response = client.get('/api/app/events/snapshot/?queue_version=10&output_token=owner-one')
    assert response.status_code == 200
    assert response.headers['Cache-Control'] == 'no-store'
    assert 'queue' not in response.json['state']
    state['output_token'] = 'owner-two'
    response = client.get('/api/app/events/snapshot/?queue_version=10&output_token=owner-one')
    assert [item['entry_id'] for item in response.json['state']['queue']] == ['first', 'second']
    assert response.json['event_epoch']
