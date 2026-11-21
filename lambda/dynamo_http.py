"""Bounded DynamoDB GetItem/PutItem using Lambda's own SigV4 credentials.

AWS credentials are used only for requests to the fixed regional AWS service.
No SDK initialization, credential export, discovery or arbitrary endpoint exists.
"""
import datetime as dt
import hashlib
import hmac
import json
import os
import re
import zlib


def sign_headers(region, operation, raw, credentials, moment):
    if not re.fullmatch(r'[a-z]{2}(?:-[a-z0-9]+)+-[0-9]+', region):
        raise ValueError('invalid AWS region')
    if operation not in ('GetItem', 'PutItem'):
        raise ValueError('unsupported DynamoDB operation')
    access, secret, token = credentials
    if not access or not secret or any('\r' in v or '\n' in v for v in (access, secret, token)):
        raise ValueError('invalid Lambda credentials')
    domain = 'amazonaws.com.cn' if region.startswith('cn-') else 'amazonaws.com'
    host = 'dynamodb.' + region + '.' + domain
    stamp = moment.strftime('%Y%m%dT%H%M%SZ')
    day = stamp[:8]
    headers = {'content-type':'application/x-amz-json-1.0', 'host':host,
               'x-amz-date':stamp, 'x-amz-target':'DynamoDB_20120810.'+operation}
    if token:
        headers['x-amz-security-token'] = token
    keys = sorted(headers)
    canonical_headers = ''.join(key+':'+headers[key].strip()+'\n' for key in keys)
    signed_headers = ';'.join(keys)
    canonical = '\n'.join(('POST','/','',canonical_headers,signed_headers,hashlib.sha256(raw).hexdigest()))
    scope = day+'/'+region+'/dynamodb/aws4_request'
    to_sign = '\n'.join(('AWS4-HMAC-SHA256',stamp,scope,hashlib.sha256(canonical.encode()).hexdigest()))
    def digest(key, value):
        return hmac.new(key,value.encode(),hashlib.sha256).digest()
    key = digest(digest(digest(digest(('AWS4'+secret).encode(),day),region),'dynamodb'),'aws4_request')
    signature = hmac.new(key,to_sign.encode(),hashlib.sha256).hexdigest()
    headers['authorization'] = 'AWS4-HMAC-SHA256 Credential='+access+'/'+scope+', SignedHeaders='+signed_headers+', Signature='+signature
    return 'https://'+host+'/', headers


class DynamoError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__('DynamoDB operation failed')


class DynamoHTTP:
    def __init__(self, region, http):
        self.region, self.http = region, http

    def _call(self, operation, payload):
        import urllib3
        raw = json.dumps(payload,separators=(',',':'),ensure_ascii=False).encode()
        if len(raw) > 600 * 1024:
            raise ValueError('DynamoDB request size')
        credentials = (os.environ['AWS_ACCESS_KEY_ID'],os.environ['AWS_SECRET_ACCESS_KEY'],
                       os.environ.get('AWS_SESSION_TOKEN',''))
        url, headers = sign_headers(self.region,operation,raw,credentials,dt.datetime.now(dt.timezone.utc))
        response = self.http.request('POST',url,body=raw,headers=headers,
            timeout=urllib3.Timeout(total=1.5),retries=False,redirect=False,preload_content=False)
        try:
            body = response.read(1024*1024+1)
            if len(body) > 1024*1024:
                raise DynamoError('ResponseTooLarge')
            checksum = getattr(response, 'headers', {}).get('x-amz-crc32')
            if checksum is not None and (not checksum.isdigit() or int(checksum) != zlib.crc32(body)):
                raise DynamoError('ChecksumMismatch')
            value = json.loads(body)
            if not isinstance(value,dict):
                raise DynamoError('InvalidResponse')
            if response.status != 200:
                code = value.get('__type','UnknownError').rsplit('#',1)[-1]
                raise DynamoError(code if re.fullmatch('[A-Za-z0-9_]{1,80}',code) else 'UnknownError')
            return value
        finally:
            response.close()
            response.release_conn()

    def get_item(self, **kwargs):
        return self._call('GetItem',kwargs)

    def put_item(self, **kwargs):
        return self._call('PutItem',kwargs)
