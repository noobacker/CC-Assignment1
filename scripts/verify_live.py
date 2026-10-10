"""Verify deployed resources and optionally run a real conversation/email request."""
import argparse
import json
import os
import sys
import time
import urllib.request
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import boto3
from settings import ROOT, deployment
sys.path.insert(0, str(ROOT / 'src'))
from common import CUISINES, search_request

def chat(url, session_id, text):
    req = urllib.request.Request(url + '/chatbot', method='POST',
        headers={'Content-Type': 'application/json', 'X-Session-Id': session_id},
        data=json.dumps({'messages': [{'type': 'unstructured', 'unstructured': {'text': text}}]}).encode())
    with urllib.request.urlopen(req, timeout=30) as response:
        body = json.load(response)
        assert response.status == 200 and body['messages'], body
        return ' '.join(m['unstructured']['text'] for m in body['messages'])

def scan_all(table):
    items, options = [], {'ConsistentRead': True}
    while True:
        page = table.scan(**options)
        items.extend(page.get('Items', []))
        if 'LastEvaluatedKey' not in page: return items
        options['ExclusiveStartKey'] = page['LastEvaluatedKey']

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--email', help='Run a real conversation that sends suggestions to this email')
    args = parser.parse_args()
    config = deployment()
    boto3.setup_default_session(region_name=config['Region'])
    rows = scan_all(boto3.resource('dynamodb').Table(config['RestaurantsTable']))
    ids = {row['BusinessID'] for row in rows}
    required = {'BusinessID', 'Name', 'Address', 'Coordinates', 'NumberOfReviews', 'Rating', 'ZipCode', 'insertedAtTimestamp'}
    assert len(rows) == len(ids) >= 1000, f'Expected 1000+ unique records; found {len(rows)}'
    assert all(required <= row.keys() for row in rows), 'Missing required restaurant fields'
    from ingest_yelp import coverage, MANHATTAN_ZIPS
    assert all(row['ZipCode'] in MANHATTAN_ZIPS for row in rows), 'Non-Manhattan ZIP code'
    report = coverage(rows)
    assert report['meetsRequirement'], report
    count = search_request('GET', '/restaurants/_count')['count']
    assert count == len(rows), f'OpenSearch count {count} differs from DynamoDB {len(rows)}'
    for cuisine in CUISINES:
        result = search_request('POST', '/restaurants/_search', {'size': 1000, 'query': {'term': {'Cuisine': cuisine}}})
        found = result['hits']['hits']
        assert found, f'No indexed restaurants for {cuisine}'
        assert all(set(h['_source']) == {'RestaurantID', 'Cuisine'} for h in found)
        assert all(h['_source']['RestaurantID'] in ids for h in found)
    mapping = search_request('GET', '/restaurants/_mapping' + ('?include_type_name=true'
        if config['SearchEngineVersion'].startswith('Elasticsearch_') else ''))
    if config['SearchEngineVersion'].startswith('Elasticsearch_'):
        assert 'Restaurant' in mapping['restaurants']['mappings'], 'Restaurant mapping type is missing'
    with urllib.request.urlopen(config['WebsiteUrl'], timeout=30) as response:
        assert response.status == 200
        assert b'Dining Concierge' in response.read()
    req = urllib.request.Request(config['ApiUrl'] + '/chatbot', method='OPTIONS',
        headers={'Origin': config['WebsiteUrl'], 'Access-Control-Request-Method': 'POST',
                 'Access-Control-Request-Headers': 'content-type,x-session-id'})
    with urllib.request.urlopen(req, timeout=30) as response:
        assert response.headers.get('Access-Control-Allow-Origin') in {'*', config['WebsiteUrl']}
    session_id = 'verify-' + str(uuid.uuid4())
    greeting = chat(config['ApiUrl'], session_id, 'hello')
    assert 'help' in greeting.lower(), greeting
    thanks = chat(config['ApiUrl'], session_id, 'thank you')
    assert 'welcome' in thanks.lower(), thanks
    rule = boto3.client('events').describe_rule(Name=next(
        r['PhysicalResourceId'] for r in boto3.client('cloudformation').list_stack_resources(
            StackName=config['StackName'])['StackResourceSummaries'] if r['LogicalResourceId'] == 'MinuteSchedule'))
    assert rule['ScheduleExpression'] == 'rate(1 minute)' and rule['State'] == 'ENABLED'
    report.update({'openSearchCount': count, 'website': 'PASS', 'apiCors': 'PASS',
                   'greetingAndThanks': 'PASS', 'oneMinuteSchedule': 'PASS', 'email': 'NOT RUN'})
    if args.email:
        tomorrow = (datetime.now(ZoneInfo('America/New_York')) + timedelta(days=1)).date().isoformat()
        transcript = []
        utterances = ['I need restaurant suggestions', 'New Delhi', 'Manhattan', 'Japanese', '2', tomorrow, '19:00', args.email]
        for message in utterances:
            answer = chat(config['ApiUrl'], session_id, message)
            transcript.append({'user': '[recipient email]' if message == args.email else message, 'bot': answer})
            print('Bot:', answer)
            if message == 'New Delhi': assert 'manhattan' in answer.lower(), answer
        assert 'email' in answer.lower() and 'shortly' in answer.lower(), answer
        state = boto3.resource('dynamodb').Table(config['StateTable'])
        deadline = time.monotonic() + 240
        previous = {}
        while time.monotonic() < deadline:
            previous = state.get_item(Key={'pk': 'USER#' + session_id}, ConsistentRead=True).get('Item', {})
            if previous.get('restaurantIds'): break
            time.sleep(10)
        assert previous.get('restaurantIds'), 'Scheduled email worker did not complete within four minutes'
        for message in ['I need restaurant suggestions', 'Manhattan', 'Japanese']:
            answer = chat(config['ApiUrl'], session_id, message)
            transcript.append({'user': message, 'bot': answer})
            print('Bot:', answer)
        assert 'same recommendations' in answer.lower(), answer
        for message in ['yes', '2', tomorrow, '19:00', args.email]:
            answer = chat(config['ApiUrl'], session_id, message)
            transcript.append({'user': '[recipient email]' if message == args.email else message, 'bot': answer})
            print('Bot:', answer)
        assert 'shortly' in answer.lower(), answer
        first_update = previous['updatedAt']
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline:
            repeated = state.get_item(Key={'pk': 'USER#' + session_id}, ConsistentRead=True).get('Item', {})
            if repeated.get('updatedAt') != first_update:
                assert repeated.get('restaurantIds') == previous['restaurantIds']
                break
            time.sleep(10)
        else:
            raise AssertionError('Repeat recommendation request was not processed')
        report.update({'email': 'SES accepted both sends; confirm receipt in inbox', 'extraCreditRepeat': 'PASS', 'transcript': transcript})
    (ROOT / 'data/live-verification.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))

if __name__ == '__main__':
    main()
