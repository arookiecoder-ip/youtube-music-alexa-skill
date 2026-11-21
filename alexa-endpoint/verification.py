"""Alexa HTTPS authentication. Verify raw bytes before parsing or dispatch."""
import base64
import datetime as dt
import json
import re
import threading
import time
import urllib.parse

import certifi
import urllib3
from OpenSSL import crypto
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa


class VerificationError(ValueError):
    pass


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise VerificationError('duplicate JSON key')
            result[key] = value
        return result
    try:
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(VerificationError('nonfinite JSON')))
        if not isinstance(value, dict):
            raise VerificationError('object required')
        return value
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise VerificationError('invalid JSON') from exc


class Verifier:
    def __init__(self, skill_id, *, roots=None, fetch=None, now=None):
        if not skill_id:
            raise ValueError('skill ID required')
        self.skill_id = skill_id
        self.roots = roots or certifi.where()
        self.http = urllib3.PoolManager(timeout=urllib3.Timeout(connect=2, read=2), retries=False)
        self.fetch = fetch or self._fetch
        self.now = now or (lambda: dt.datetime.now(dt.timezone.utc))
        self.cache = {}
        self.lock = threading.Lock()

    @staticmethod
    def certificate_url(url):
        if (not isinstance(url, str) or len(url) > 2048
                or any(ord(char) <= 32 or ord(char) >= 127 for char in url)):
            raise VerificationError('certificate URL')
        try:
            u = urllib.parse.urlsplit(url)
            valid = (u.scheme == 'https' and u.hostname == 's3.amazonaws.com'
                     and u.port in (None, 443) and not u.username and not u.password
                     and not u.query and not u.fragment
                     and re.fullmatch(r'/echo\.api/[A-Za-z0-9_./-]+', u.path)
                     and all(s not in ('.', '..', '') for s in u.path.split('/')[2:]))
        except ValueError:
            valid = False
        if not valid:
            raise VerificationError('certificate URL')
        return url

    def _fetch(self, url):
        response = self.http.request('GET', url, redirect=False, preload_content=False)
        try:
            if response.status != 200:
                raise VerificationError('certificate unavailable')
            body = response.read(65537)
            if len(body) > 65536:
                raise VerificationError('certificate too large')
            return body
        finally:
            response.close()
            response.release_conn()

    def _certificate(self, url):
        now = self.now()
        with self.lock:
            cached = self.cache.get(url)
            if cached and now < cached[1]:
                return cached[0]
        try:
            pem = self.fetch(url)
            if not isinstance(pem, bytes) or len(pem) > 65536:
                raise VerificationError('certificate too large')
            chain = x509.load_pem_x509_certificates(pem)
            if not chain or len(chain) > 8:
                raise VerificationError('certificate chain')
            leaf = chain[0]
            store = crypto.X509Store()
            store.load_locations(self.roots)
            store.set_time(now)
            crypto.X509StoreContext(store, crypto.X509.from_cryptography(leaf),
                                   [crypto.X509.from_cryptography(c) for c in chain[1:]]).verify_certificate()
            if not (leaf.not_valid_before_utc <= now < leaf.not_valid_after_utc):
                raise VerificationError('certificate validity')
            names = leaf.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
            if 'echo-api.amazon.com' not in names.get_values_for_type(x509.DNSName):
                raise VerificationError('certificate identity')
            try:
                if not leaf.extensions.get_extension_for_class(x509.KeyUsage).value.digital_signature:
                    raise VerificationError('certificate usage')
            except x509.ExtensionNotFound:
                pass
            key = leaf.public_key()
            if not isinstance(key, rsa.RSAPublicKey) or key.key_size < 2048:
                raise VerificationError('certificate key')
            expires = min(*(c.not_valid_after_utc for c in chain), now + dt.timedelta(hours=1))
            with self.lock:
                if len(self.cache) >= 16:
                    self.cache.pop(next(iter(self.cache)))
                self.cache[url] = (key, expires)
            return key
        except VerificationError:
            raise
        except Exception as exc:
            raise VerificationError('certificate validation') from exc

    def verify(self, raw, headers):
        if not isinstance(raw, bytes) or not 0 < len(raw) <= 512 * 1024:
            raise VerificationError('body size')
        url = self.certificate_url(headers.get('SignatureCertChainUrl', ''))
        signature = headers.get('Signature-256', '')
        if not isinstance(signature, str) or len(signature) > 2048:
            raise VerificationError('signature')
        try:
            signature = base64.b64decode(signature, validate=True)
            self._certificate(url).verify(signature, raw, padding.PKCS1v15(), hashes.SHA256())
        except VerificationError:
            raise
        except Exception as exc:
            raise VerificationError('signature verification') from exc
        event = strict_json(raw)
        try:
            stamp = event['request']['timestamp']
            if not isinstance(stamp, str) or not stamp.endswith('Z'):
                raise ValueError('timestamp')
            stamp = dt.datetime.fromisoformat(stamp[:-1] + '+00:00')
            if abs((self.now() - stamp).total_seconds()) > 150:
                raise ValueError('timestamp')
            context_app = event['context']['System']['application']['applicationId']
            session_app = event.get('session', {}).get('application', {}).get('applicationId', context_app)
            if context_app != self.skill_id or session_app != self.skill_id:
                raise ValueError('application')
            request_id = event['request']['requestId']
            if not isinstance(request_id, str) or not 1 <= len(request_id) <= 512:
                raise ValueError('request ID')
            actor = event['context']['System']
            for value in (actor['user']['userId'], actor['device']['deviceId']):
                if not isinstance(value, str) or not 1 <= len(value) <= 1024:
                    raise ValueError('actor')
        except (KeyError, TypeError, ValueError, OverflowError, AttributeError) as exc:
            raise VerificationError('request envelope') from exc
        return event
