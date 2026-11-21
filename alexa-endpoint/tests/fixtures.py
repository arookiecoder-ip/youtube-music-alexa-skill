import datetime as dt
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
