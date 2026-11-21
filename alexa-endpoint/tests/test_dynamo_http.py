"""Compare the small signer against AWS botocore and test bounded failures."""
import datetime as dt
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest
from unittest import mock
import zlib
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'lambda'))
from dynamo_http import sign_headers,DynamoHTTP,DynamoError


class Signing(unittest.TestCase):
    def test_signatures_match_aws_botocore_for_regions_tokens_unicode_and_conditions(self):
        from botocore.auth import SigV4Auth
        from botocore.awsrequest import AWSRequest
        from botocore.credentials import Credentials
        when=dt.datetime(2026,11,21,4,0,0,tzinfo=dt.timezone.utc)
        for region in ('us-east-1','eu-west-1','us-west-2','cn-north-1'):
            for token in ('','synthetic-temporary-token'):
                for operation, payload in (
                    ('GetItem',{'TableName':'test','Key':{'id':{'S':'echo-v2:test'}},'ConsistentRead':True}),
                    ('PutItem',{'TableName':'test','Item':{'title':{'S':'नमस्ते'}},'ConditionExpression':'#r = :old','ExpressionAttributeNames':{'#r':'revision'},'ExpressionAttributeValues':{':old':{'S':'previous'}}})):
                    with self.subTest(region=region,token=bool(token),operation=operation):
                        raw=json.dumps(payload,separators=(',',':'),ensure_ascii=False).encode()
                        creds=('AKIDEXAMPLE','synthetic-secret',token)
                        url,headers=sign_headers(region,operation,raw,creds,when)
                        req=AWSRequest(method='POST',url=url,data=raw,headers={'Content-Type':'application/x-amz-json-1.0','X-Amz-Target':'DynamoDB_20120810.'+operation})
                        with mock.patch('botocore.auth.get_current_datetime',return_value=when):
                            SigV4Auth(Credentials(*creds),'dynamodb',region).add_auth(req)
                        self.assertEqual(headers['authorization'],req.headers['Authorization'])

    def test_invalid_destination_operations_and_credentials_fail_closed(self):
        when=dt.datetime.now(dt.timezone.utc)
        for region in ('evil.example','us-east-1/path','us-east-1\nX: evil','', 'https://evil'):
            with self.subTest(region=region),self.assertRaises(ValueError):
                sign_headers(region,'GetItem',b'{}',('a','b','c'),when)
        with self.assertRaises(ValueError):sign_headers('us-east-1','Scan',b'{}',('a','b','c'),when)
        with self.assertRaises(ValueError):sign_headers('us-east-1','GetItem',b'{}',('a\nb','b','c'),when)

    def test_bounded_protocol_errors_checksums_and_no_automatic_retry(self):
        env={'AWS_ACCESS_KEY_ID':'synthetic-key','AWS_SECRET_ACCESS_KEY':'synthetic-secret','AWS_SESSION_TOKEN':'synthetic-token'}
        for status,body,checksum,error in (
            (200,b'{"Item":{}}',None,None),
            (200,b'{}',str(zlib.crc32(b'{}')),None),
            (400,b'{"__type":"com.amazonaws#ConditionalCheckFailedException"}',None,DynamoError),
            (400,b'{"__type":"com.amazonaws#AccessDeniedException"}',None,DynamoError),
            (500,b'{}',None,DynamoError),
            (302,b'{}',None,DynamoError),
            (200,b'[]',None,DynamoError),
            (200,b'bad-json',None,ValueError),
            (200,b'{}','0',DynamoError),
            (200,b'x'*(1024*1024+1),None,DynamoError)):
            response=mock.Mock(status=status,headers={} if checksum is None else {'x-amz-crc32':checksum})
            response.read.return_value=body
            http=mock.Mock();http.request.return_value=response
            client=DynamoHTTP('eu-west-1',http)
            with self.subTest(status=status,bodylen=len(body),checksum=checksum),mock.patch.dict(os.environ,env):
                if error:
                    with self.assertRaises(error):client.get_item(TableName='test',Key={'id':{'S':'key'}})
                else:self.assertIsInstance(client.get_item(TableName='test',Key={'id':{'S':'key'}}),dict)
            http.request.assert_called_once()
            args=http.request.call_args
            self.assertEqual(args.args[1],'https://dynamodb.eu-west-1.amazonaws.com/')
            self.assertFalse(args.kwargs['redirect']);self.assertFalse(args.kwargs['retries'])
            self.assertIn('authorization',args.kwargs['headers'])
            response.read.assert_called_once_with(1024*1024+1)
            response.close.assert_called_once();response.release_conn.assert_called_once()

    def test_missing_credentials_or_oversize_never_calls_network(self):
        http=mock.Mock();client=DynamoHTTP('eu-west-1',http)
        with mock.patch.dict(os.environ,{},clear=True),self.assertRaises(KeyError):client.get_item(TableName='test')
        with self.assertRaises(ValueError):client.put_item(Item={'large':'x'*(600*1024)})
        http.request.assert_not_called()
