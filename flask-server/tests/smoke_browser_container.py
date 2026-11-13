"""CI-only smoke test, run inside the browser image without real credentials."""
import base64
import json
from pathlib import Path
import socket
import time
import urllib.request
import urllib.error

assert next(line for line in Path('/proc/1/status').read_text().splitlines() if line.startswith('Uid:')).split()[1] == '10001'
for port in (5900,6080):
    with socket.socket() as connection:
        connection.settimeout(1)
        assert connection.connect_ex(('127.0.0.1',port)) != 0, 'Unauthenticated desktop listener exposed'
try:
    urllib.request.urlopen('http://127.0.0.1:8765/interactive/frame')
    raise AssertionError('Anonymous frame read accepted')
except urllib.error.HTTPError as error:
    assert error.code == 401
headers={'Content-Type':'application/json','X-Control-Token':'isolated-ci-browser-token',
         'X-Auth-Attempt':'isolated-ci-browser-attempt'}
request=urllib.request.Request('http://127.0.0.1:8765/interactive/start',data=b'{}',headers=headers)
assert urllib.request.urlopen(request,timeout=15).status in (202,409)
for attempt in range(60):
    try:
        response=urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:8765/interactive/frame',headers=headers),timeout=5)
        frame=json.load(response)
        assert frame['image'].startswith('data:image/jpeg;base64,')
        assert base64.b64decode(frame['image'].split(',',1)[1]).startswith(b'\xff\xd8')
        assert frame['width']>0 and frame['height']>0
        break
    except urllib.error.HTTPError as error:
        assert error.code in (409,503), error.code
        time.sleep(.5)
else:
    raise AssertionError('Chromium touch surface did not become ready')
request=urllib.request.Request('http://127.0.0.1:8765/interactive/close',data=b'{}',headers=headers)
assert urllib.request.urlopen(request,timeout=15).status == 200
print('Non-root Chromium, authenticated JPEG surface and retired desktop ports verified')
