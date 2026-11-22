"""Build recommendation-only browser headers from a download cookie export."""
import hashlib
import http.cookiejar
import time
import urllib.request


def cookie_headers(path):
    jar = http.cookiejar.MozillaCookieJar(str(path))
    jar.load(ignore_discard=True, ignore_expires=False)
    request = urllib.request.Request('https://music.youtube.com/')
    jar.add_cookie_header(request)
    cookie = request.get_header('Cookie') or ''
    values = dict(part.strip().split('=', 1) for part in cookie.split(';') if '=' in part)
    sapisid = values.get('SAPISID') or values.get('__Secure-3PAPISID')
    if not sapisid:
        raise ValueError('The download cookie export has no browser authentication cookie')
    origin = 'https://music.youtube.com'
    stamp = str(int(time.time()))
    digest = hashlib.sha1(f'{stamp} {sapisid} {origin}'.encode()).hexdigest()
    return {'cookie': cookie, 'authorization': f'SAPISIDHASH {stamp}_{digest}',
            'origin': origin, 'x-origin': origin, 'x-goog-authuser': '0',
            'content-type': 'application/json'}
