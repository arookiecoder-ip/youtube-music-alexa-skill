import json
import os
from pathlib import Path
import sys
import time
from unittest import TestCase, mock
from types import SimpleNamespace as NS
sys.path[:0] = [str(Path(__file__).resolve().parents[2] / 'lambda')]
import state_migration


class Export(TestCase):
    def setUp(self):
        self.instruction = dict(account_id='a', skill_id='s', expires=time.time()+600,
                               url='https://music.example/alexa/skill/migration/', token='x'*43, transfer_id='t')
        self.data = mock.patch.multiple(state_migration.data, MUSICBOX_ACCOUNT_ID='a', MUSICBOX_SERVER_URL='https://music.example')
        self.data.start();self.addCleanup(self.data.stop)
        self.env = mock.patch.dict(os.environ, DYNAMODB_PERSISTENCE_REGION='us-east-1', DYNAMODB_PERSISTENCE_TABLE_NAME='table')
        self.env.start();self.addCleanup(self.env.stop)

    def test_scans_all_pages_and_posts_only_to_pinned_receiver(self):
        db = mock.Mock()
        db.scan.side_effect = [{'Items': [{'id': {'S':'a'}, 'attributes': {'M':{}}}], 'LastEvaluatedKey': {'id':{'S':'a'}}}, {'Items': []}]
        http = mock.Mock()
        http.request.return_value = NS(status=200, data=json.dumps({'ok':True, 'count':1, 'digest':'hash'}).encode())
        with mock.patch('boto3.client', return_value=db), mock.patch('urllib3.PoolManager', return_value=http):
            self.assertEqual(state_migration._transfer(self.instruction, 's')['count'], 1)
        self.assertEqual(db.scan.call_count, 2)
        self.assertTrue(db.scan.call_args.kwargs['ConsistentRead'])
        self.assertFalse(http.request.call_args.kwargs['redirect'])
        self.assertNotIn('token', json.loads(http.request.call_args.kwargs['body']))
        self.assertEqual(db.method_calls[0][0], 'scan')

    def test_scope_errors_never_access_aws(self):
        for field, value in [('account_id', 'other'), ('skill_id', 'other'), ('expires',0),
                             ('url','https://evil/alexa/skill/migration/'),
                             ('url','http://music.example/alexa/skill/migration/')]:
            with self.subTest(field=field), mock.patch('boto3.client') as aws, self.assertRaises(ValueError):
                state_migration._transfer(dict(self.instruction, **{field:value}), 's')
            aws.assert_not_called()

    def test_incomplete_or_denied_scan_never_activates_receiver(self):
        db = mock.Mock()
        for effect in (RuntimeError('denied'), None):
            db.scan.side_effect = effect
            db.scan.return_value = {'Items': [], 'LastEvaluatedKey': {'id': {'S':'x'}}}
            with self.subTest(), mock.patch('boto3.client',return_value=db), mock.patch('urllib3.PoolManager') as http, self.assertRaises(RuntimeError):
                state_migration._transfer(self.instruction, 's')
            http.assert_not_called()

    def test_receiver_errors_fail_export(self):
        db = mock.Mock();db.scan.return_value = {'Items':[]}
        for response in (NS(status=503,data=b''), NS(status=200,data=b'{"ok":true,"count":1}')):
            http=mock.Mock();http.request.return_value=response
            with mock.patch('boto3.client',return_value=db), mock.patch('urllib3.PoolManager',return_value=http), self.assertRaises(RuntimeError):
                state_migration._transfer(self.instruction,'s')

    def test_restore_scope_checked_before_any_write(self):
        class Response:
            status=200
            def read(self, _):
                return json.dumps({'account_id':'other','skill_id':'s','transfer_id':'t','items':[]}).encode()
            def close(self): pass
            def release_conn(self): pass
        db=mock.Mock();http=mock.Mock();http.request.return_value=Response()
        with mock.patch('boto3.client',return_value=db), mock.patch('urllib3.PoolManager',return_value=http), self.assertRaises(ValueError):
            state_migration._transfer(dict(self.instruction,action='restore'),'s')
        db.put_item.assert_not_called()

    def test_restore_validates_all_rows_before_writing(self):
        for rows in ([{'id':{'S':'a'},'attributes':{'M':{}}}, {'broken':{}}],
                     [{'id':{'S':'a'},'attributes':{'M':{}}}]*2,
                     [{'id':{'S':'a'},'attributes':{'M':{}}},{'id':{'S':'b'},'attributes':{'M':{'bad':{'INVALID':'x'}}}}]):
            class Response:
                status=200
                def read(self, _):
                    return json.dumps({'account_id':'a','skill_id':'s','transfer_id':'t','items':rows}).encode()
                def close(self): pass
                def release_conn(self): pass
            db=mock.Mock();http=mock.Mock();http.request.return_value=Response()
            with mock.patch('boto3.client',return_value=db), mock.patch('urllib3.PoolManager',return_value=http), self.assertRaises((ValueError,KeyError,TypeError)):
                state_migration._transfer(dict(self.instruction,action='restore'),'s')
            db.put_item.assert_not_called()

    def test_restore_writes_only_own_table(self):
        row={'id':{'S':'a'},'attributes':{'M':{}}}
        class Response:
            status=200
            def read(self, _):
                return json.dumps({'account_id':'a','skill_id':'s','transfer_id':'t','items':[row]}).encode()
            def close(self): pass
            def release_conn(self): pass
        db=mock.Mock();http=mock.Mock();http.request.return_value=Response()
        with mock.patch('boto3.client',return_value=db), mock.patch('urllib3.PoolManager',return_value=http):
            self.assertEqual(state_migration._transfer(dict(self.instruction,action='restore'),'s')['count'],1)
        db.put_item.assert_called_once_with(TableName='table',Item=row)

    def test_export_reports_only_sanitized_failure(self):
        http=mock.Mock()
        with mock.patch.object(state_migration,'_transfer',side_effect=RuntimeError('private detail')), mock.patch('urllib3.PoolManager',return_value=http), self.assertRaises(RuntimeError):
            state_migration.export_state(self.instruction,'s')
        self.assertEqual(http.request.call_count,2)
        body=json.loads(http.request.call_args.kwargs['body'])
        self.assertEqual(body['phase'],'failed')
        self.assertEqual(body['code'],'RuntimeError')
        self.assertNotIn('private detail',str(body))
        self.assertNotIn('token',body)

    def test_wrapper_rejects_scope_before_reporting_or_aws(self):
        with mock.patch('boto3.client') as aws, mock.patch('urllib3.PoolManager') as http, self.assertRaises(ValueError):
            state_migration.export_state(dict(self.instruction,account_id='evil'),'s')
        aws.assert_not_called();http.assert_not_called()

    def test_device_export_needs_only_getitem(self):
        key='echo-v2:'+'a'*64
        row={'id':{'S':key},'attributes':{'M':{'saved_playlists':{'L':[]}}}}
        db=mock.Mock();db.get_item.return_value={'Item':row}
        http=mock.Mock();http.request.return_value=NS(status=200,data=b'{"ok":true,"count":1,"digest":"hash"}')
        with mock.patch('boto3.client',return_value=db), mock.patch('urllib3.PoolManager',return_value=http):
            self.assertEqual(state_migration._transfer(dict(self.instruction,mode='device',expected_devices=2),'s',key)['count'],1)
        db.scan.assert_not_called();db.put_item.assert_not_called()
        db.get_item.assert_called_once_with(TableName='table',Key={'id':{'S':key}},ConsistentRead=True)
        payload=json.loads(http.request.call_args.kwargs['body'])
        self.assertEqual(payload['device_key'],key)
        self.assertEqual(payload['expected_devices'],2)
