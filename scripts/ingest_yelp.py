"""Collect genuine Yelp businesses, deduplicate by ID, and load DynamoDB/OpenSearch."""
import argparse
from collections import Counter
from decimal import Decimal
import json
import os
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import boto3
from settings import ROOT, load_env, deployment
sys.path.insert(0, str(ROOT / 'src'))
from common import CUISINES, now_iso, search_request

CATEGORIES = {'chinese': 'chinese', 'japanese': 'japanese', 'italian': 'italian',
              'mexican': 'mexican', 'indian': 'indpak', 'thai': 'thai', 'french': 'french',
              'american': 'newamerican,tradamerican'}
MANHATTAN_ZIPS = {str(n) for n in range(10001, 10041)} | {'10044', '10065', '10069', '10075', '10128', '10280', '10282'}
NEIGHBORHOODS = ['Manhattan, NY', 'Upper West Side, Manhattan, NY', 'Upper East Side, Manhattan, NY',
                 'Harlem, Manhattan, NY', 'East Village, Manhattan, NY', 'Chelsea, Manhattan, NY',
                 'Financial District, Manhattan, NY', 'Washington Heights, Manhattan, NY']

def normalize(business, cuisine, timestamp=None):
    location = business.get('location') or {}
    coordinates = business.get('coordinates') or {}
    if (business.get('is_closed') or location.get('zip_code') not in MANHATTAN_ZIPS or
        location.get('state') != 'NY' or not business.get('id') or not business.get('name') or
        coordinates.get('latitude') is None or coordinates.get('longitude') is None):
        return None
    address = ', '.join(location.get('display_address') or
                        [location.get('address1') or '', location.get('city') or '', location.get('zip_code') or ''])
    if not location.get('address1') or business.get('rating') is None or business.get('review_count') is None:
        return None
    return {'BusinessID': business['id'], 'Name': business['name'], 'Address': address,
            'Coordinates': coordinates, 'NumberOfReviews': business['review_count'],
            'Rating': business['rating'], 'ZipCode': location['zip_code'],
            'Cuisine': [cuisine], 'insertedAtTimestamp': timestamp or now_iso()}

def fetch(key, params):
    url = 'https://api.yelp.com/v3/businesses/search?' + urllib.parse.urlencode(params)
    for attempt in range(5):
        try:
            req = urllib.request.Request(url, headers={'Authorization': 'Bearer ' + key})
            with urllib.request.urlopen(req, timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            if exc.code in {429, 500, 502, 503, 504} and attempt < 4:
                time.sleep(min(2 ** (attempt + 1), 30))
                continue
            raise RuntimeError(f'Yelp returned HTTP {exc.code}. Check API access/quota; collected data is cached.') from None
    raise RuntimeError('Yelp retries exhausted')

def coverage(records):
    counts = Counter(c for r in records for c in r['Cuisine'])
    return {'uniqueRestaurants': len(records), 'byCuisine': dict(sorted(counts.items())),
            'meetsRequirement': len(records) >= 1000 and sum(v >= 180 for v in counts.values()) >= 5}

def collect(path, per_cuisine):
    key = os.environ.get('YELP_API_KEY')
    if not key:
        raise SystemExit('Set YELP_API_KEY in .env. No fabricated restaurant records are used.')
    records = {r['BusinessID']: r for r in json.loads(path.read_text())} if path.exists() else {}
    rng = random.Random(2026)
    for cuisine in CUISINES:
        places = list(NEIGHBORHOODS)
        rng.shuffle(places)
        count = sum(cuisine in r['Cuisine'] for r in records.values())
        for location in places:
            if count >= per_cuisine: break
            for offset in range(0, 240, 50):
                if count >= per_cuisine: break
                payload = fetch(key, {'term': cuisine + ' restaurants', 'categories': CATEGORIES[cuisine],
                    'location': location, 'limit': min(50, 240 - offset), 'offset': offset, 'sort_by': 'best_match'})
                businesses = payload.get('businesses', [])
                rng.shuffle(businesses)
                for business in businesses:
                    if count >= per_cuisine: break
                    row = normalize(business, cuisine)
                    if row is None: continue
                    old = records.get(row['BusinessID'])
                    if old:
                        if cuisine not in old['Cuisine']:
                            old['Cuisine'].append(cuisine)
                            count += 1
                    else:
                        records[row['BusinessID']] = row
                        count += 1
                path.write_text(json.dumps(list(records.values()), indent=2) + '\n')
                print(f'{cuisine}: {count}; unique total: {len(records)}', flush=True)
                if len(businesses) < 50 or offset + 50 >= min(payload.get('total', 0), 240): break
                time.sleep(0.25)
    return list(records.values())

def load(records, config):
    properties = {'RestaurantID': {'type': 'keyword'}, 'Cuisine': {'type': 'keyword'}}
    typed = config['SearchEngineVersion'].startswith('Elasticsearch_')
    path = '/restaurants' + ('?include_type_name=true' if typed else '')
    mapping = {'Restaurant': {'properties': properties}} if typed else {'properties': properties, '_meta': {'entity': 'Restaurant'}}
    try:
        search_request('PUT', path, {'settings': {'number_of_shards': 1, 'number_of_replicas': 0}, 'mappings': mapping})
    except urllib.error.HTTPError as exc:
        body = exc.read().decode()
        if exc.code != 400 or 'resource_already_exists_exception' not in body: raise
    dynamodb = boto3.resource('dynamodb').Table(config['RestaurantsTable'])
    inserted_at = now_iso()
    with dynamodb.batch_writer(overwrite_by_pkeys=['BusinessID']) as batch:
        for row in records:
            item = dict(row, insertedAtTimestamp=inserted_at)
            batch.put_item(Item=json.loads(json.dumps(item), parse_float=Decimal))
    for start in range(0, len(records), 200):
        lines = []
        for row in records[start:start + 200]:
            meta = {'_index': 'restaurants', '_id': row['BusinessID']}
            if typed: meta['_type'] = 'Restaurant'
            lines.extend([json.dumps({'index': meta}), json.dumps({'RestaurantID': row['BusinessID'], 'Cuisine': row['Cuisine']})])
        result = search_request('POST', '/_bulk', '\n'.join(lines) + '\n')
        if result.get('errors'):
            errors = [item for item in result['items'] if item.get('index', {}).get('error')]
            raise RuntimeError('OpenSearch bulk import failed: ' + json.dumps(errors[:3]))
    search_request('POST', '/restaurants/_refresh')

def main():
    load_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--collect-only', action='store_true')
    parser.add_argument('--load-only', action='store_true')
    parser.add_argument('--per-cuisine', type=int, default=220)
    args = parser.parse_args()
    path = ROOT / 'data/restaurants.json'
    if args.load_only and not path.exists(): parser.error('No cached Yelp data. Collect first.')
    records = json.loads(path.read_text()) if args.load_only else collect(path, args.per_cuisine)
    report = coverage(records)
    (ROOT / 'data/ingestion-report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))
    if not report['meetsRequirement']:
        raise SystemExit('Dataset is below 1,000 unique restaurants or five cuisines with about 200 each. Resolve Yelp coverage/quota before submission.')
    if not args.collect_only:
        load(records, deployment())
        print('DynamoDB and OpenSearch import complete. Run scripts/verify_live.py.')

if __name__ == '__main__':
    main()
