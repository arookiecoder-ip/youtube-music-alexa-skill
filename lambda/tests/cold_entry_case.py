"""Fresh-process SDK integration with only DynamoDB I/O and HTTP substituted."""
import json,sys,time,types
from pathlib import Path
from types import SimpleNamespace as Node
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import data
import skill_bootstrap
import lambda_function
assert 'boto3' not in sys.modules and 'skill_runtime' not in sys.modules

data.DEFAULT_API_URL='https://offline.example'
data.MUSICBOX_EMAIL='test@example.test'
data.MUSICBOX_ALEXA_TOKEN='synthetic-token'
meta={'title':'Selected song','artist':'Artist','video_id':'AAAAAAAAAAA','thumbnail':None,'duration_ms':180000}
payload={'binding':'a'*64,'play':{'video_id':'AAAAAAAAAAA','offset_ms':15000,'kind':'play','startup':{
    'song_info':{'metadata':meta,'stream':{'audio_url':'https://offline.example/audio'}},
    'playlist':[meta],'queue_index':0}}}
http_calls=[];saved=[];auth_done=[]
class Client:
    def request(self,method,url,**kwargs):
        http_calls.append(url)
        assert 'include_play=1' in url
        time.sleep(.3)  # deterministic blocking network time, not a timing assertion
        auth_done.append(time.monotonic())
        return Node(status=200,data=json.dumps(payload).encode())
class Persistence:
    def __init__(self,**kwargs):self.partition_keygen=kwargs['partition_keygen']
    def get_attributes(self,request_envelope):
        assert auth_done and len(request_envelope._musicbox_binding)==64
        assert self.partition_keygen(request_envelope).startswith('echo-v2:')
        return {'amazon-user':{'playback_setting':{'loop':False,'shuffle':False},
            'playback_info':{'index':0,'play_order':[],'offset_in_ms':0},'playlist':[],
            'saved_playlists':{'Favorites':{'id':'kept','title':'Favorites'}},'api_url':None}}
    def save_attributes(self,request_envelope,attributes):saved.append(attributes)
parent=types.ModuleType('ask_sdk_dynamodb');parent.__path__=[]
adapter=types.ModuleType('ask_sdk_dynamodb.adapter');adapter.DynamoDbAdapter=Persistence
sys.modules['ask_sdk_dynamodb']=parent;sys.modules['ask_sdk_dynamodb.adapter']=adapter
skill_bootstrap.http_client=lambda:Client()
event={'version':'1.0','context':{'System':{'application':{'applicationId':'private-skill'},
    'user':{'userId':'amazon-user'},'device':{'deviceId':'echo','supportedInterfaces':{'AudioPlayer':{}}},
    'apiEndpoint':'https://api.amazonalexa.com','apiAccessToken':'synthetic-proof'}},
    'request':{'type':'IntentRequest','requestId':'cold-request','timestamp':'2026-11-20T12:00:00Z','locale':'en-US',
      'intent':{'name':'PlaySongIntent','slots':{'songName':{'name':'songName','value':'silentmode the app selection'}}}}}
start=time.monotonic()
context=Node(aws_request_id='cold-lambda') if len(sys.argv)<2 or sys.argv[1]!='serial' else None
response=lambda_function.lambda_handler(event,context)
elapsed=(time.monotonic()-start)*1000
assert len(http_calls)==1 and len(saved)==1,response
assert saved[0]['amazon-user']['saved_playlists']['Favorites']['id']=='kept'
stream=response['response']['directives'][0]['audioItem']['stream']
assert stream['offsetInMilliseconds']==15000 and stream['token'].endswith('|b'+'a'*64),response
print(json.dumps({'handler_ms':round(elapsed),'http_calls':len(http_calls),'mode':'parallel' if context else 'serial'}))
