"""Local configuration; never prints credentials."""
import json
import os
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]

def load_env():
    path = ROOT / '.env'
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            key, sep, value = line.partition('=')
            if sep:
                os.environ.setdefault(key.strip(), value.strip().strip('\"\''))

def deployment():
    load_env()
    path = ROOT / 'deployment.json'
    if not path.exists():
        raise SystemExit('Deploy first: deployment.json is missing. See README.md.')
    data = json.loads(path.read_text())
    os.environ.setdefault('AWS_DEFAULT_REGION', data['Region'])
    os.environ['OPENSEARCH_ENDPOINT'] = data['SearchEndpoint']
    return data
