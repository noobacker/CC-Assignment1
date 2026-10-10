"""Shared AWS clients and signed OpenSearch HTTP requests; no third-party runtime packages."""
import json
import os
import urllib.request
from datetime import datetime, timezone
from functools import lru_cache
import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

CUISINES = ('chinese', 'japanese', 'italian', 'mexican', 'indian', 'thai', 'french', 'american')

@lru_cache
def client(service):
    return boto3.client(service)

@lru_cache
def table(name):
    return boto3.resource('dynamodb').Table(name)

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def search_request(method, path, body=None):
    endpoint = os.environ['OPENSEARCH_ENDPOINT'].rstrip('/')
    if not endpoint.startswith('https://'):
        endpoint = 'https://' + endpoint
    raw = None if body is None else (body if isinstance(body, str) else json.dumps(body)).encode()
    headers = {'Content-Type': 'application/json'}
    session = boto3.Session()
    req = AWSRequest(method=method, url=endpoint + path, data=raw, headers=headers)
    SigV4Auth(session.get_credentials().get_frozen_credentials(), 'es',
              session.region_name or os.environ.get('AWS_REGION', 'us-east-1')).add_auth(req)
    request = urllib.request.Request(req.url, data=raw, headers=dict(req.headers), method=method)
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read() or '{}')
