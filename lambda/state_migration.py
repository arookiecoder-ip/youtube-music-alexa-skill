"""Read-only DynamoDB export executed inside the existing hosted execution role."""
import json
import time
from urllib.parse import urlsplit
import data


def _transfer(instruction, application_id):
    target = urlsplit(instruction['url'])
    origin = urlsplit(data.MUSICBOX_SERVER_URL)
    if (instruction['account_id'] != data.MUSICBOX_ACCOUNT_ID
            or instruction['skill_id'] != application_id
            or not time.time() < instruction['expires'] <= time.time() + 3600
            or target.scheme != 'https' or target.netloc != origin.netloc
            or target.username or target.password or target.query or target.fragment
            or target.path != '/alexa/skill/migration/'):
        raise ValueError('invalid migration scope')
    import boto3
    import os
    import urllib3
    client = boto3.client('dynamodb', region_name=os.environ['DYNAMODB_PERSISTENCE_REGION'])
    if instruction.get('action', 'export') == 'restore':
        http = urllib3.PoolManager()
        response = http.request('GET', instruction['url'],
            headers={'X-MusicBox-Migration': instruction['token']},
            timeout=urllib3.Timeout(total=8), retries=False, redirect=False,
            preload_content=False)
        try:
            raw = response.read(12 * 1024 * 1024 + 1)
            if response.status != 200 or len(raw) > 12 * 1024 * 1024:
                raise RuntimeError('restore receiver unavailable')
        finally:
            response.close()
            response.release_conn()
        payload = json.loads(raw)
        if any(payload.get(k) != instruction[k] for k in ('account_id', 'skill_id', 'transfer_id')):
            raise ValueError('restore scope mismatch')
        rows = payload['items']
        if not isinstance(rows, list) or len(rows) > 10000:
            raise ValueError('restore size')
        # Validate the entire snapshot before the first write. Retry is idempotent;
        # keep both endpoints quiescent until every row has been acknowledged.
        keys = set()
        for row in rows:
            key = row['id']['S']
            if not isinstance(key, str) or not key or key in keys or set(row['attributes']) != {'M'}:
                raise ValueError('restore row')
            keys.add(key)
        for row in rows:
            client.put_item(TableName=os.environ['DYNAMODB_PERSISTENCE_TABLE_NAME'], Item=row)
        return {'count': len(rows), 'digest': 'restored'}
    if instruction.get('action', 'export') != 'export':
        raise ValueError('invalid migration action')
    items, cursor, size = [], None, 0
    started = time.monotonic()
    # All-or-nothing snapshot; never activate a truncated scan. This deliberately
    # fails safely for large tables instead of spending unbounded Lambda time.
    for _ in range(20):
        kwargs = {'TableName': os.environ['DYNAMODB_PERSISTENCE_TABLE_NAME'], 'ConsistentRead': True}
        if cursor:
            kwargs['ExclusiveStartKey'] = cursor
        page = client.scan(**kwargs)
        rows = page.get('Items', [])
        size += len(json.dumps(rows).encode())
        items.extend(rows)
        if size > 12 * 1024 * 1024 or len(items) > 10000 or time.monotonic() - started > 20:
            raise RuntimeError('migration snapshot exceeds safe bounds')
        cursor = page.get('LastEvaluatedKey')
        if not cursor:
            break
    if cursor:
        raise RuntimeError('migration snapshot incomplete')
    payload = {key: instruction[key] for key in ('account_id', 'skill_id', 'transfer_id')}
    payload['items'] = items
    http = urllib3.PoolManager()
    response = http.request('POST', instruction['url'], body=json.dumps(payload).encode(),
                            headers={'Content-Type': 'application/json', 'X-MusicBox-Migration': instruction['token']},
                            timeout=urllib3.Timeout(total=8), retries=False, redirect=False)
    if response.status != 200:
        raise RuntimeError('migration receiver rejected snapshot')
    result = json.loads(response.data)
    if not result.get('ok') or result.get('count') != len(items):
        raise RuntimeError('migration acknowledgement mismatch')
    return {'count': len(items), 'digest': result['digest']}


def export_state(instruction, application_id):
    # Revalidate the destination before even sending diagnostics to it.
    target, origin = urlsplit(instruction['url']), urlsplit(data.MUSICBOX_SERVER_URL)
    if (instruction['account_id'] != data.MUSICBOX_ACCOUNT_ID or instruction['skill_id'] != application_id
            or not time.time() < instruction['expires'] <= time.time() + 3600
            or target.scheme != 'https' or target.netloc != origin.netloc
            or target.username or target.password or target.query or target.fragment
            or target.path != '/alexa/skill/migration/'):
        raise ValueError('invalid migration scope')
    def report(phase, code=''):
        try:
            import urllib3
            payload = {key: instruction[key] for key in ('account_id', 'skill_id', 'transfer_id')}
            payload.update(phase=phase, code=code)
            urllib3.PoolManager().request('POST', instruction['url'], body=json.dumps(payload).encode(),
                headers={'Content-Type':'application/json','X-MusicBox-Migration':instruction['token']},
                timeout=urllib3.Timeout(total=2), retries=False, redirect=False)
        except Exception:
            pass
    report('started')
    try:
        return _transfer(instruction, application_id)
    except Exception as exc:
        code = getattr(exc, 'response', {}).get('Error', {}).get('Code', type(exc).__name__)
        report('failed', code)
        raise
