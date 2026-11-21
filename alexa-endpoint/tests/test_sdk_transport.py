"""Exercise actual ASK dispatch and prove successful relay loads no ASK in Lambda."""
import os
from pathlib import Path
import subprocess
import sys
import unittest
ROOT=Path(__file__).resolve().parents[2]


class SDKTransport(unittest.TestCase):
    def run_case(self,code):
        env=dict(os.environ,PYTHONPATH=os.pathsep.join((str(ROOT/'lambda'),str(ROOT/'alexa-endpoint'),str(ROOT/'alexa-endpoint/tests'))),
                 MUSICBOX_SERVER_URL='https://music.example',MUSICBOX_ACCOUNT_ID='account',
                 MUSICBOX_RUNTIME_SECRET='x'*64,MUSICBOX_PERSISTENCE_MODE='relay',AWS_EC2_METADATA_DISABLED='true',DYNAMODB_PERSISTENCE_TABLE_NAME='own-table',DYNAMODB_PERSISTENCE_REGION='eu-west-1')
        p=subprocess.run([sys.executable,'-c',code],env=env,capture_output=True,text=True,timeout=15)
        self.assertEqual(p.returncode,0,p.stdout+p.stderr)

    def test_real_sdk_launch_preserves_attributes_with_no_aws_initialization(self):
        self.run_case('''
import json,tempfile
from pathlib import Path
from test_endpoint import payload,signed,SECRET,SKILL
from service import create_app
from relay_store import ReplayLedger
import skill_runtime
with tempfile.TemporaryDirectory() as d:
 ledger=ReplayLedger(str(Path(d)/'requests.db'),'account',SKILL)
 app=create_app(skill_runtime.dynamodb_adapter,skill_runtime.lambda_handler,ledger,secret=SECRET,account_id='account',skill_id=SKILL)
 p=payload();raw,headers=signed(p)
 r=app.test_client().post('/alexa/skill/relay/',data=raw,headers=headers)
 assert r.status_code==200,r.status_code
 value=r.json
 assert value['response']['response']['outputSpeech']
 assert value['dirty']
 assert value['key']==p['key']
 assert 'attributes' in value
 assert skill_runtime.dynamodb_adapter.local.__dict__=={}
''')

    def test_relay_does_not_import_the_ask_sdk_in_lambda(self):
        self.run_case('''
import json,sys
from types import SimpleNamespace as NS
from unittest.mock import Mock,patch
import lambda_function,skill_bootstrap,runtime_relay
from fixtures import event,SECRET,BINDING
http=Mock()
def send(method,url,**kw):
 raw=json.dumps({'dirty':False,'response':{'version':'1.0','response':{}}}).encode()
 h=kw['headers']
 return NS(status=200,data=raw,headers={'X-MusicBox-Relay-Signature':runtime_relay.signature(SECRET,h['X-MusicBox-Relay-Time'],h['X-MusicBox-Relay-Nonce'],raw,response=True)})
http.request.side_effect=send
db=Mock();db.get_item.return_value={}
with patch.object(runtime_relay,'_ddb',return_value=db),patch.object(skill_bootstrap,'_fetch',return_value=NS(status=200,data=json.dumps({'binding':BINDING}).encode())),patch.object(skill_bootstrap,'http_client',return_value=http):
 response=lambda_function.lambda_handler(event(),None)
 assert response['version']=='1.0'
 assert lambda_function._runtime is None
 assert not any(k.startswith('ask_sdk') for k in sys.modules)
''')

    def test_denied_authorization_uses_existing_sdk_error_without_reading_state(self):
        self.run_case('''
import json
from types import SimpleNamespace as NS
from unittest.mock import Mock,patch
import lambda_function,skill_bootstrap,runtime_relay
from fixtures import event
with patch.object(runtime_relay,'_ddb') as db,patch.object(skill_bootstrap,'_fetch',return_value=NS(status=403,data=json.dumps({'error':{'code':'alexa_access_denied','message':'Access denied'}}).encode())):
 response=lambda_function.lambda_handler(event(),None)
 assert response['response']['outputSpeech']
 db.assert_not_called()
 assert not getattr(skill_bootstrap._pending,'item',None)
''')

    def test_actual_dynamo_http_relay_imports_no_aws_or_ask_sdk(self):
        self.run_case('''
import json,sys,os
from types import SimpleNamespace as NS
from unittest.mock import Mock,patch
import lambda_function,skill_bootstrap,runtime_relay
from fixtures import event,SECRET,BINDING
os.environ.update(AWS_ACCESS_KEY_ID='synthetic-key',AWS_SECRET_ACCESS_KEY='synthetic-aws-secret',AWS_SESSION_TOKEN='synthetic-session-token')
http=Mock();aws_calls=[]
def send(method,url,**kw):
 if url.startswith('https://dynamodb.'):
  aws_calls.append(url)
  return NS(status=200,headers={},read=lambda n:b'{}',close=lambda:None,release_conn=lambda:None)
 assert 'synthetic-aws-secret' not in str(kw)
 assert 'synthetic-session-token' not in str(kw)
 raw=json.dumps({'dirty':False,'response':{'version':'1.0','response':{}}}).encode()
 h=kw['headers']
 return NS(status=200,data=raw,headers={'X-MusicBox-Relay-Signature':runtime_relay.signature(SECRET,h['X-MusicBox-Relay-Time'],h['X-MusicBox-Relay-Nonce'],raw,response=True)})
http.request.side_effect=send
with patch.object(skill_bootstrap,'_fetch',return_value=NS(status=200,data=json.dumps({'binding':BINDING}).encode())),patch.object(skill_bootstrap,'http_client',return_value=http):
 response=lambda_function.lambda_handler(event(),None)
 assert response['version']=='1.0'
 assert lambda_function._runtime is None
 assert len(aws_calls)==1
 assert not any(k.startswith(('ask_sdk','boto3','botocore')) for k in sys.modules)
''')
