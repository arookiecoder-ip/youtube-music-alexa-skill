"""Offline audio transport fixture; production authentication still runs."""
import io
from youtube_stream import Relay


def fake_relay(*args, **kwargs):
    body = io.BytesIO(b'test-audio')
    body.read1 = body.read
    body.status = 200
    body.headers = {'Content-Type':'audio/mp4', 'Content-Length':'10'}
    return Relay(body, b'', lambda:None)
