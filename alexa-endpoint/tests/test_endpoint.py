import base64
import concurrent.futures
import datetime as dt
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace as NS
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'alexa-endpoint'), str(ROOT / 'lambda')]
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import NameOID
from persistence import SQLiteAdapter
from service import ActorLocks, create_app
from verification import Verifier, VerificationError, strict_json

NOW = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
SKILL = 'amzn1.ask.skill.test'
URL = 'https://s3.amazonaws.com/echo.api/echo-api-cert.pem'


def event(request_id='r1'):
    return {'context': {'System': {'application': {'applicationId': SKILL},
                                   'user': {'userId': 'user'}, 'device': {'deviceId': 'device'}}},
            'request': {'requestId': request_id, 'type': 'LaunchRequest',
                        'timestamp': NOW.isoformat().replace('+00:00', 'Z')}}


def envelope(skill=SKILL, binding='binding', device='device'):
    return NS(context=NS(system=NS(application=NS(application_id=skill),
                                  user=NS(user_id='user'), device=NS(device_id=device))),
              _musicbox_binding=binding)


class Security(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Test root')])
        cls.name = name
        cls.root = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
                    .public_key(cls.ca_key.public_key()).serial_number(1)
                    .not_valid_before(NOW - dt.timedelta(days=1)).not_valid_after(NOW + dt.timedelta(days=10))
                    .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
                    .sign(cls.ca_key, hashes.SHA256()))
        cls.roots = Path(cls.tmp.name) / 'roots.pem'
        cls.roots.write_bytes(cls.root.public_bytes(serialization.Encoding.PEM))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def cert(self, san='echo-api.amazon.com', start=-1, end=1, signer=None, usage=True):
        return (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, san)]))
                .issuer_name(self.name).public_key(self.key.public_key()).serial_number(2)
                .not_valid_before(NOW + dt.timedelta(days=start)).not_valid_after(NOW + dt.timedelta(days=end))
                .add_extension(x509.BasicConstraints(ca=False, path_length=None), True)
                .add_extension(x509.SubjectAlternativeName([x509.DNSName(san)]), False)
                .add_extension(x509.KeyUsage(usage, False, True, False, False, False, False, False, False), True)
                .sign(signer or self.ca_key, hashes.SHA256()).public_bytes(serialization.Encoding.PEM))

    def verifier(self, cert=None):
        return Verifier(SKILL, roots=str(self.roots), fetch=lambda _: cert or self.cert(), now=lambda: NOW)

    def signed(self, obj=None, raw=None):
        raw = raw if raw is not None else json.dumps(obj or event()).encode()
        signature = self.key.sign(raw, padding.PKCS1v15(), hashes.SHA256())
        return raw, {'SignatureCertChainUrl': URL, 'Signature-256': base64.b64encode(signature).decode()}

    def test_valid_signature(self):
        raw, headers = self.signed()
        self.assertEqual(self.verifier().verify(raw, headers), event())

    def test_certificate_urls(self):
        for url in ('http://s3.amazonaws.com/echo.api/a', 'https://s3.amazonaws.com.evil/echo.api/a',
                    'https://evil/echo.api/a', 'https://user@s3.amazonaws.com/echo.api/a',
                    'https://s3.amazonaws.com:444/echo.api/a', 'https://s3.amazonaws.com/echo.api/../a',
                    'https://s3.amazonaws.com/echo.api/%2e/a', URL + '?x=1', URL + '#x',
                    'https://s3.amazonaws.com/echo.api//a', 'https://s3.amazonaws.com/echo.api/./a'):
            with self.subTest(url=url), self.assertRaises(VerificationError):
                Verifier.certificate_url(url)

    def test_certificate_failures(self):
        for cert in (self.cert(san='evil.test'), self.cert(start=-3, end=-1),
                     self.cert(start=1, end=2), self.cert(usage=False), b'broken',
                     self.cert(signer=rsa.generate_private_key(public_exponent=65537, key_size=2048))):
            with self.subTest(), self.assertRaises(VerificationError):
                self.verifier(cert).verify(*self.signed())

    def test_tamper_missing_bad_signature(self):
        raw, headers = self.signed()
        for body, hdr in ((raw + b' ', headers), (raw, {}),
                          (raw, dict(headers, **{'Signature-256': '!!!'})),
                          (raw, dict(headers, **{'Signature-256': ''})),
                          (raw, {'SignatureCertChainUrl': URL, 'Signature': headers['Signature-256']})):
            with self.subTest(), self.assertRaises(VerificationError):
                self.verifier().verify(body, hdr)

    def test_envelope_failures(self):
        for change in ('old', 'future', 'skill', 'session', 'missing', 'empty', 'session_null', 'timestamp'):
            obj = event()
            if change in ('old', 'future'):
                obj['request']['timestamp'] = (NOW + dt.timedelta(seconds=151 if change == 'future' else -151)).isoformat().replace('+00:00', 'Z')
            elif change == 'skill':
                obj['context']['System']['application']['applicationId'] = 'evil'
            elif change == 'session':
                obj['session'] = {'application': {'applicationId': 'evil'}}
            elif change == 'session_null':
                obj['session'] = None
            elif change == 'missing':
                del obj['context']['System']['device']
            elif change == 'empty':
                obj['request']['requestId'] = ''
            else:
                obj['request']['timestamp'] = 'nope'
            with self.subTest(change=change), self.assertRaises(VerificationError):
                self.verifier().verify(*self.signed(obj))

    def test_strict_json(self):
        for raw in (b'{"a":1,"a":2}', b'{"a":NaN}', b'[]', b'null', b'\xff', b'{'):
            with self.subTest(raw=raw), self.assertRaises(VerificationError):
                strict_json(raw)

    def test_cache_and_size(self):
        v = self.verifier()
        v.verify(*self.signed())
        v.fetch = lambda _: (_ for _ in ()).throw(AssertionError('cached'))
        v.verify(*self.signed())
        with self.assertRaises(VerificationError):
            v.verify(b'x' * (512 * 1024 + 1), {})


class Storage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / 'state.db')
        self.db = SQLiteAdapter(self.path, 'account', SKILL)

    def tearDown(self):
        self.tmp.cleanup()

    def test_import_roundtrip_restart_and_scope(self):
        from boto3.dynamodb.types import TypeSerializer
        from decimal import Decimal
        from skill_security import persistence_key
        env = envelope()
        attrs = {'user': {'saved_playlists': ['a'], 'offset': Decimal('123.50')}, 'unknown': {'v': True}}
        items = [{'id': {'S': persistence_key(env)}, 'attributes': TypeSerializer().serialize(attrs)}]
        self.assertFalse(self.db.ready())
        digest = self.db.import_snapshot(items, 't1')
        self.assertEqual(self.db.import_snapshot(items, 't1'), digest)
        restarted = SQLiteAdapter(self.path, 'account', SKILL)
        self.assertEqual(restarted.get_attributes(env), attrs)
        self.assertEqual(restarted.get_attributes(envelope(device='other')), {})
        self.assertEqual(restarted.get_attributes(envelope(binding='other')), {})
        self.assertFalse(SQLiteAdapter(self.path, 'other', SKILL).ready())
        with self.assertRaises(ValueError):
            restarted.get_attributes(envelope(skill='evil'))
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)

    def test_no_partial_import_or_overwrite(self):
        for items in ([{'id': {'S': 'a'}, 'attributes': {'M': {}}}] * 2,
                      [{'id': {'S': 'a'}, 'attributes': {'M': {}}}, {'broken': {}}]):
            with self.assertRaises((ValueError, KeyError)):
                self.db.import_snapshot(items, 't')
            self.assertFalse(self.db.ready())
        self.db.import_snapshot([], 't')
        with self.assertRaises(ValueError):
            self.db.import_snapshot([], 'different')

    def test_write_read_delete_and_closed_before_import(self):
        with self.assertRaises(RuntimeError):
            self.db.get_attributes(envelope())
        with self.assertRaises(RuntimeError):
            self.db.save_attributes(envelope(), {})
        self.db.import_snapshot([], 't')
        self.db.save_attributes(envelope(), {'x': 1})
        self.assertEqual(self.db.get_attributes(envelope()), {'x': 1})
        self.db.delete_attributes(envelope())
        self.assertEqual(self.db.get_attributes(envelope()), {})

    def test_request_replay_atomic_and_restart(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            claims = list(pool.map(lambda _: self.db.claim_request('r', 'hash', 1000), range(32)))
        self.assertEqual(sum(claims), 1)
        self.assertFalse(SQLiteAdapter(self.path, 'account', SKILL).claim_request('r', 'hash', 1001))
        with self.assertRaises(ValueError):
            self.db.claim_request('r', 'different', 1001)
        self.assertTrue(self.db.claim_request('r', 'hash', 1601))

    def test_service_readiness_migration_errors_replay_and_failure(self):
        class V:
            def verify(self, raw, headers):
                if raw == b'bad':
                    raise VerificationError()
                return event(raw.decode())
        calls = []
        def handler(e, _):
            calls.append(e)
            if e['request']['requestId'] == 'fail':
                raise RuntimeError('private token must not be logged')
            return {'version': '1.0', 'response': {}}
        app = create_app(self.db, handler, V(), migration_token='secret',
                         migration_expires=time.time()+60, transfer_id='t')
        c = app.test_client()
        self.assertEqual(c.get('/health/ready').status_code, 503)
        self.assertEqual(c.post('/alexa/skill/', data=b'bad').status_code, 400)
        self.assertEqual(c.post('/alexa/skill/', data=b'r').status_code, 503)
        payload = {'account_id': 'account', 'skill_id': SKILL, 'transfer_id': 't', 'items': []}
        self.assertEqual(c.post('/alexa/skill/migration/', json=payload).status_code, 404)
        self.assertEqual(c.post('/alexa/skill/migration/', json=payload,
                               headers={'X-MusicBox-Migration': 'secret'}).status_code, 200)
        self.assertEqual(c.get('/health/ready').status_code, 200)
        self.assertEqual(c.post('/alexa/skill/', data=b'r').status_code, 200)
        self.assertEqual(c.post('/alexa/skill/', data=b'r').status_code, 200)
        self.assertEqual(len(calls), 1)
        with self.assertLogs('service', level='ERROR') as logs:
            self.assertEqual(c.post('/alexa/skill/', data=b'fail').status_code, 503)
        self.assertNotIn('private token', str(logs.output))
        self.assertEqual(c.get('/alexa/skill/').status_code, 405)

    def test_fleet_import_remains_unready_until_all_devices_and_preserves_playlists(self):
        first, second = 'echo-v2:'+'a'*64, 'echo-v2:'+'b'*64
        row = {'id':{'S':first}, 'attributes':{'M':{'saved_playlists':{'L':[{'S':'playlist'}]}}}}
        digest = self.db.import_device_snapshot([row], 't', first, 2)
        self.assertFalse(self.db.ready())
        self.assertEqual(self.db.import_device_snapshot([row], 't', first, 2), digest)
        self.assertFalse(self.db.ready())
        with self.assertRaises(ValueError):
            self.db.import_device_snapshot([row], 't', second, 2)
        self.assertFalse(self.db.ready())
        self.db.import_device_snapshot([], 't', second, 2)
        self.assertTrue(self.db.ready())
        self.assertEqual(self.db.export_snapshot(), [row])
        self.db.import_device_snapshot([row], 't', first, 2)
        with self.assertRaises(ValueError):
            self.db.import_device_snapshot([], 'other', second, 2)

    def test_canary_never_initializes_an_unmigrated_device(self):
        from skill_security import persistence_key, SkillAuthorizationError
        env = envelope()
        key = persistence_key(env)
        self.db.import_device_snapshot([], 't', key, 1)
        self.db.require_imported = True
        self.assertEqual(self.db.get_attributes(env), {})
        with self.assertRaises(SkillAuthorizationError):
            self.db.get_attributes(envelope(device='unmigrated'))
        with self.assertRaises(SkillAuthorizationError):
            self.db.save_attributes(envelope(device='unmigrated'), {})

    def test_actor_lock_capacity_timeout_and_cleanup(self):
        locks = ActorLocks(capacity=1)
        self.assertEqual(locks.acquire('a'), 'a')
        self.assertIsNone(locks.acquire('b'))
        self.assertIsNone(locks.acquire('a', timeout=0))
        locks.release('a')
        self.assertEqual(locks.acquire('b'), 'b')
        locks.release('b')
        self.assertEqual(locks.entries, {})


if __name__ == '__main__':
    unittest.main()
