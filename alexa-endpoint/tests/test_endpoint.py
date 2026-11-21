import concurrent.futures
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace as NS
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'alexa-endpoint'), str(ROOT / 'lambda')]
from relay_store import RelayAdapter, ReplayLedger
from runtime_relay import signature, state_key
from service import ActorLocks, create_app, strict_json
import runtime_relay
import skill_bootstrap

SKILL='amzn1.ask.skill.test'
SECRET='x'*64
BINDING='a'*64


def event(request_id='r1', device='device', kind='LaunchRequest'):
    return {'version':'1.0', 'session':{'new':True,'sessionId':'test-session',
        'application':{'applicationId':SKILL}, 'user':{'userId':'user'},'attributes':{}},
        'context':{'System':{'application':{'applicationId':SKILL},'user':{'userId':'user'},
        'device':{'deviceId':device,'supportedInterfaces':{'AudioPlayer':{}}},
        'apiAccessToken':'proof', 'apiEndpoint':'https://api.eu.amazonalexa.com'}},
        'request':{'requestId':request_id,'type':kind,'timestamp':dt.datetime.now(dt.timezone.utc).isoformat().replace('+00:00','Z'),'locale':'en-US'}}


def envelope(device='device', binding=BINDING):
    return NS(context=NS(system=NS(application=NS(application_id=SKILL),
        user=NS(user_id='user'),device=NS(device_id=device))),_musicbox_binding=binding)


def payload(e=None):
    e=e or event()
    return {'event':e,'account_id':'account','identity':{'binding':BINDING},
        'params':skill_bootstrap._params(e),'key':state_key(e,BINDING),'attributes':{'M':{}}}


def signed(p, *, secret=SECRET, stamp=None, nonce='b'*32, raw=None):
    raw=raw if raw is not None else json.dumps(p,separators=(',',':')).encode()
    stamp=str(int(time.time())) if stamp is None else stamp
    return raw, {'X-MusicBox-Relay-Time':stamp,'X-MusicBox-Relay-Nonce':nonce,
                 'X-MusicBox-Relay-Signature':signature(secret,stamp,nonce,raw),'Content-Type':'application/json'}


class Runtime(unittest.TestCase):
    def setUp(self):
        self.config=mock.patch.multiple(runtime_relay.data,MUSICBOX_SERVER_URL='https://music.example',DEFAULT_API_URL='https://music.example',MUSICBOX_ACCOUNT_ID='account',MUSICBOX_RUNTIME_SECRET=SECRET)
        self.config.start();self.addCleanup(self.config.stop)
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path=str(Path(self.tmp.name)/'requests.db')
        self.ledger=ReplayLedger(self.path,'account',SKILL)
        self.adapter=RelayAdapter()
        self.calls=[]
        def handler(e,ctx):
            self.calls.append(e)
            self.adapter.save_attributes(envelope(e['context']['System']['device']['deviceId']),{'saved_playlists':['kept'],'position':123})
            return {'version':'1.0','response':{'shouldEndSession':True}}
        self.app=create_app(self.adapter,handler,self.ledger,secret=SECRET,account_id='account',skill_id=SKILL)
        self.client=self.app.test_client()

    def post(self,p=None,**kwargs):
        raw,headers=signed(p or payload(),**kwargs)
        return self.client.post('/alexa/skill/relay/',data=raw,headers=headers)

    def test_authenticated_dispatch_and_response_authentication(self):
        p=payload();raw,headers=signed(p)
        r=self.client.post('/alexa/skill/relay/',data=raw,headers=headers)
        self.assertEqual(r.status_code,200)
        self.assertTrue(r.json['dirty'])
        self.assertEqual(r.json['key'],p['key'])
        self.assertEqual(r.json['attributes']['M']['position'],{'N':'123'})
        self.assertEqual(r.headers['X-MusicBox-Relay-Signature'],signature(SECRET,headers['X-MusicBox-Relay-Time'],headers['X-MusicBox-Relay-Nonce'],r.data,response=True))
        self.assertEqual(r.headers['Cache-Control'],'no-store')
        self.assertFalse(hasattr(self.adapter.local,'state'))
        self.assertIsNone(skill_bootstrap.take(p['event']['request']['requestId'],skill_bootstrap._api_url(),p['params']))

    def test_missing_tampered_expired_or_wrong_secret_never_dispatches(self):
        raw,headers=signed(payload())
        tests=[(raw,{}),(raw+b' ',headers),signed(payload(),secret='other'),
               signed(payload(),stamp=str(int(time.time())-31)),
               signed(payload(),stamp=str(int(time.time())+31)),
               signed(payload(),nonce='not-valid')]
        for body,hdr in tests:
            with self.subTest():
                self.assertEqual(self.client.post('/alexa/skill/relay/',data=body,headers=hdr).status_code,403)
        self.assertEqual(self.calls,[])

    def test_skill_account_binding_device_and_session_scope(self):
        for change in ('account','skill','session','binding','device','key','params'):
            p=payload()
            if change=='account':p['account_id']='other'
            elif change=='skill':p['event']['context']['System']['application']['applicationId']='other'
            elif change=='session':p['event']['session']['application']['applicationId']='other'
            elif change=='binding':p['identity']['binding']='b'*64
            elif change=='device':p['event']['context']['System']['device']['deviceId']='other'
            elif change=='key':p['key']='echo-v2:'+'b'*64
            else:p['params']={}
            with self.subTest(change=change):self.assertIn(self.post(p).status_code,(400,403))
        self.assertEqual(self.calls,[])

    def test_malformed_and_oversized_requests_fail_before_dispatch(self):
        for raw in (b'{}',b'[]',b'null',b'{"a":1,"a":2}',b'{"a":NaN}',b'\xff',b'['*2000):
            with self.subTest():self.assertEqual(self.post(raw=raw).status_code,400)
        p=payload();p['attributes']={'M':{'x':{'UNKNOWN':'bad'}}}
        self.assertEqual(self.post(p).status_code,400)
        self.assertEqual(self.post(raw=b'x'*(1024*1024+1)).status_code,413)
        self.assertEqual(self.calls,[])

    def test_event_timestamps_and_shape(self):
        for change in ('old','future','none','session_null'):
            p=payload()
            if change in ('old','future'):
                p['event']['request']['timestamp']=(dt.datetime.now(dt.timezone.utc)+dt.timedelta(seconds=151 if change=='future' else -151)).isoformat().replace('+00:00','Z')
            elif change=='none':p['event']['request']['timestamp']=None
            else:p['event']['session']=None
            with self.subTest():self.assertEqual(self.post(p).status_code,400)
        self.assertEqual(self.calls,[])

    def test_duplicate_suppression_survives_restart_and_body_conflict(self):
        p=payload();self.assertEqual(self.post(p).status_code,200)
        self.assertFalse(self.post(p).json['dirty']);self.assertEqual(len(self.calls),1)
        self.ledger=ReplayLedger(self.path,'account',SKILL)
        self.assertFalse(self.ledger.claim('r1',hashlib.sha256(signed(p)[0]).hexdigest(),time.time()))
        p['attributes']={'M':{'x':{'S':'changed'}}}
        self.assertEqual(self.post(p).status_code,400)
        self.assertEqual(len(self.calls),1)

    def test_storage_account_isolation_and_atomic_claim(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            claimed=list(pool.map(lambda _:self.ledger.claim('same','hash',time.time()),range(32)))
        self.assertEqual(sum(claimed),1)
        self.assertTrue(ReplayLedger(self.path,'other',SKILL).claim('same','other-hash',time.time()))
        self.assertEqual(os.stat(self.path).st_mode & 0o777,0o600)

    def test_adapter_binding_scope_and_cleanup_on_exception(self):
        with self.assertRaises(RuntimeError):
            with self.adapter.request(state_key(event(),BINDING),{'M':{}}):
                with self.assertRaises(ValueError):self.adapter.get_attributes(envelope(device='other'))
                self.assertEqual(self.adapter.get_attributes(envelope()),{})
                raise RuntimeError('test')
        self.assertFalse(hasattr(self.adapter.local,'state'))
        with self.assertRaises(ValueError):self.adapter.get_attributes(envelope())

    def test_adapter_keeps_unknown_fields_decimals_and_saved_playlists(self):
        from decimal import Decimal
        value={'future':{'n':Decimal('12.34')},'saved_playlists':['a','b'], 'binary':b'\x00\xff', 'binary_set':{b'a',b'b'}, 'string_set':{'a','b'}, 'number_set':{Decimal('1'),Decimal('2')}}
        with self.adapter.request(state_key(event(),BINDING),{'M':{}}) as state:
            self.adapter.save_attributes(envelope(),value)
            self.assertEqual(self.adapter.get_attributes(envelope()),value)
            self.assertTrue(state['dirty'])
            self.adapter.delete_attributes(envelope());self.assertEqual(state['attributes'],{'M':{}})

    def test_two_echo_requests_keep_attributes_in_separate_threads(self):
        import threading
        barrier=threading.Barrier(2)
        def run(device):
            e=event(device=device)
            with self.adapter.request(state_key(e,BINDING),{'M':{}}) as state:
                self.adapter.save_attributes(envelope(device=device),{'device':device})
                barrier.wait(timeout=2)
                value=self.adapter.get_attributes(envelope(device=device))
                return value,state['key']
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            a=pool.submit(run,'first');b=pool.submit(run,'second')
            first,second=a.result(),b.result()
        self.assertEqual(first[0],{'device':'first'})
        self.assertEqual(second[0],{'device':'second'})
        self.assertNotEqual(first[1],second[1])

    def test_binary_dynamodb_wire_values_are_validated_without_corruption(self):
        p=payload();p['attributes']={'M':{'opaque':{'B':'AP8='},'set':{'BS':['YQ==','Yg==']}}}
        self.assertEqual(self.post(p).status_code,200)
        p=payload(event('different'));p['attributes']={'M':{'opaque':{'B':'invalid base64!'}}}
        self.assertEqual(self.post(p).status_code,400)

    def test_actor_limits_timeout_and_cleanup(self):
        locks=ActorLocks(capacity=1)
        self.assertEqual(locks.acquire('a'),'a')
        self.assertIsNone(locks.acquire('b'))
        self.assertIsNone(locks.acquire('a',timeout=0))
        locks.release('a');self.assertEqual(locks.acquire('b'),'b');locks.release('b')
        self.assertEqual(locks.entries,{})

    def test_handler_failure_has_no_sensitive_log_or_response(self):
        def fail(e,c):raise RuntimeError('private token')
        app=create_app(self.adapter,fail,self.ledger,secret=SECRET,account_id='account',skill_id=SKILL)
        raw,headers=signed(payload())
        with self.assertLogs('service',level='ERROR') as logs:
            r=app.test_client().post('/alexa/skill/relay/',data=raw,headers=headers)
        self.assertEqual(r.status_code,503)
        self.assertNotIn('private token',str(logs.output));self.assertEqual(r.data,b'')
        self.assertFalse(hasattr(self.adapter.local,'state'))

    def test_no_unauthenticated_direct_skill_or_migration_route(self):
        self.assertEqual(self.client.post('/alexa/skill/',json={}).status_code,404)
        self.assertEqual(self.client.post('/alexa/skill/migration/',json={}).status_code,404)
        self.assertEqual(self.client.get('/health/ready').status_code,200)


class LambdaRelay(unittest.TestCase):
    def setUp(self):
        self.config=mock.patch.multiple(runtime_relay.data,MUSICBOX_RUNTIME_SECRET=SECRET,
            MUSICBOX_SERVER_URL='https://music.example',DEFAULT_API_URL='https://music.example',MUSICBOX_ACCOUNT_ID='account')
        self.config.start();self.addCleanup(self.config.stop)
        self.env=mock.patch.dict(os.environ,DYNAMODB_PERSISTENCE_TABLE_NAME='own-table',DYNAMODB_PERSISTENCE_REGION='eu-west-1')
        self.env.start();self.addCleanup(self.env.stop)

    def invoke(self, *, result=None, row=None, status=200, tamper=False, ddb_error=None, remaining=8000):
        e=event();self.db=mock.Mock();self.db.get_item.return_value={'Item':row} if row else {}
        self.db.put_item.side_effect=ddb_error
        key=state_key(e,BINDING)
        result=result or {'response':{'version':'1.0','response':{}},'key':key,'dirty':True,'attributes':{'M':{'saved':{'S':'yes'}}}}
        http=mock.Mock();self.http=http
        def send(method,url,**kwargs):
            self.sent=json.loads(kwargs['body'])
            raw=json.dumps(result).encode();h=kwargs['headers']
            sig=signature(SECRET,h['X-MusicBox-Relay-Time'],h['X-MusicBox-Relay-Nonce'],raw,response=True)
            return NS(status=status,data=raw,headers={'X-MusicBox-Relay-Signature':'wrong' if tamper else sig})
        http.request.side_effect=send
        response=NS(status=200,data=json.dumps({'binding':BINDING,'control':{'kind':'resume'}}).encode())
        with mock.patch.object(runtime_relay,'_ddb',return_value=self.db), mock.patch.object(skill_bootstrap,'_fetch',return_value=response), mock.patch.object(skill_bootstrap,'http_client',return_value=http):
            return runtime_relay.invoke(e,NS(get_remaining_time_in_millis=lambda:remaining))

    def test_authorize_read_dispatch_then_conditional_write(self):
        self.invoke()
        self.db.get_item.assert_called_once()
        self.assertTrue(self.db.get_item.call_args.kwargs['ConsistentRead'])
        kwargs=self.db.put_item.call_args.kwargs
        self.assertEqual(kwargs['TableName'],'own-table')
        self.assertEqual(kwargs['ConditionExpression'],'attribute_not_exists(#r)')
        self.assertEqual(self.sent['identity']['control']['kind'],'resume')

    def test_provider_work_gets_time_within_alexa_and_durable_save_budget(self):
        self.invoke()
        timeout=self.http.request.call_args.kwargs['timeout']
        self.assertGreater(timeout.total,4)
        self.assertLessEqual(timeout.total,6)

    def test_exhausted_lambda_budget_never_dispatches_or_writes(self):
        with self.assertRaises(TimeoutError):self.invoke(remaining=1000)
        self.http.request.assert_not_called()
        self.db.put_item.assert_not_called()

    def test_existing_revision_and_unknown_item_fields_preserved(self):
        self.invoke(row={'musicbox_runtime_revision':{'S':'old'},'future':{'S':'kept'},'attributes':{'M':{}}})
        kwargs=self.db.put_item.call_args.kwargs
        self.assertEqual(kwargs['ExpressionAttributeValues'],{':previous':{'S':'old'}})
        self.assertEqual(kwargs['Item']['future'],{'S':'kept'})

    def test_denial_does_not_read_or_dispatch(self):
        with mock.patch.object(skill_bootstrap,'_fetch',return_value=NS(status=403,data=b'{}')), mock.patch.object(runtime_relay,'_ddb') as db, self.assertRaises(runtime_relay.RelayAuthorizationDenied):
            runtime_relay.invoke(event(),None)
        db.assert_not_called()

    def test_failed_or_tampered_response_never_writes(self):
        for options in ({'status':503},{'tamper':True},
                        {'result':{'dirty':True,'key':'other','attributes':{'M':{}},'response':{}}}):
            with self.subTest(),self.assertRaises((RuntimeError,ValueError)):
                self.invoke(**options)
            self.db.put_item.assert_not_called()

    def test_conditional_write_failure_does_not_return_play_directive(self):
        with self.assertRaises(RuntimeError):self.invoke(ddb_error=RuntimeError('conditional conflict'))

    def test_read_only_or_duplicate_reply_never_writes(self):
        self.invoke(result={'dirty':False,'response':{'version':'1.0','response':{}}})
        self.db.put_item.assert_not_called()

    def test_no_aws_access_for_invalid_binding(self):
        with mock.patch.object(skill_bootstrap,'_fetch',return_value=NS(status=200,data=b'{"binding":"invalid"}')), mock.patch.object(runtime_relay,'_ddb') as db, self.assertRaises(ValueError):
            runtime_relay.invoke(event(),None)
        db.assert_not_called()


if __name__=='__main__':unittest.main()
