"""Scheduled queue worker: OpenSearch IDs -> DynamoDB details -> SES email."""
import hashlib
import json
import logging
import os
import time
import uuid
from botocore.exceptions import ClientError
from common import client, table, search_request, now_iso

log = logging.getLogger(__name__)

def recommendations(request):
    ids = request.get('restaurantIds')
    if not ids:
        seed = int(hashlib.sha256(request['requestId'].encode()).hexdigest()[:7], 16)
        result = search_request('POST', '/restaurants/_search', {
            'size': 10, '_source': ['RestaurantID', 'Cuisine'],
            'query': {'function_score': {'query': {'term': {'Cuisine': request['cuisine']}},
                      'random_score': {'seed': seed, 'field': '_seq_no'}, 'boost_mode': 'replace'}}})
        ids = [hit['_source']['RestaurantID'] for hit in result['hits']['hits']]
    restaurants = []
    for business_id in dict.fromkeys(ids):
        item = table(os.environ['RESTAURANTS_TABLE']).get_item(Key={'BusinessID': business_id}).get('Item')
        if item:
            restaurants.append(item)
        if len(restaurants) == 3:
            break
    if not restaurants:
        raise RuntimeError('No restaurants available for requested cuisine; message retained for retry.')
    return restaurants

def email_text(request, restaurants):
    lines = [f"Hello! Here are my {request['cuisine'].title()} restaurant suggestions for "
             f"{request['numberOfPeople']} people, for {request['diningDate']} at "
             f"{request['diningTime']} (New York time):", '']
    for i, item in enumerate(restaurants, 1):
        lines.append(f"{i}. {item['Name']}, located at {item['Address']} "
                     f"(rating: {item['Rating']}/5, {item['NumberOfReviews']} reviews).")
    lines.extend(['', 'Enjoy your meal!', 'These are suggestions, not confirmed reservations.'])
    return '\n'.join(lines)

def process(request):
    state = table(os.environ['STATE_TABLE'])
    key = {'pk': 'REQUEST#' + request['requestId']}
    existing = state.get_item(Key=key, ConsistentRead=True).get('Item', {})
    if existing.get('status') == 'SENT':
        return
    restaurants = recommendations({**request, **({'restaurantIds': existing['restaurantIds']}
                                                    if existing.get('restaurantIds') else {})})
    ids = [r['BusinessID'] for r in restaurants]
    # Persist selected IDs before sending, so a retry uses the same recommendations.
    state.put_item(Item={**key, 'status': 'PENDING', 'restaurantIds': ids,
                        'expiresAt': int(time.time()) + 7 * 86400})
    sent = client('ses').send_email(Source=os.environ['SES_SENDER_EMAIL'],
        Destination={'ToAddresses': [request['email']]},
        Message={'Subject': {'Data': 'Your Manhattan dining suggestions', 'Charset': 'UTF-8'},
                 'Body': {'Text': {'Data': email_text(request, restaurants), 'Charset': 'UTF-8'}}})
    state.put_item(Item={'pk': 'USER#' + request['userId'], 'location': request['location'],
                        'cuisine': request['cuisine'], 'restaurantIds': ids, 'updatedAt': now_iso(),
                        'expiresAt': int(time.time()) + 90 * 86400})
    state.put_item(Item={**key, 'status': 'SENT', 'restaurantIds': ids,
                        'sesMessageId': sent['MessageId'], 'sentAt': now_iso(),
                        'expiresAt': int(time.time()) + 7 * 86400})

def poll(event, context):
    sqs = client('sqs')
    messages = sqs.receive_message(QueueUrl=os.environ['QUEUE_URL'], MaxNumberOfMessages=5,
                                  WaitTimeSeconds=1, VisibilityTimeout=180).get('Messages', [])
    succeeded = failed = 0
    failed_groups = set()
    for message in messages:
        if context and context.get_remaining_time_in_millis() < 25000:
            break
        try:
            request = json.loads(message['Body'])
            if request['userId'] in failed_groups:
                continue
            process(request)
            sqs.delete_message(QueueUrl=os.environ['QUEUE_URL'], ReceiptHandle=message['ReceiptHandle'])
            succeeded += 1
        except Exception:
            failed += 1
            try:
                failed_groups.add(json.loads(message['Body'])['userId'])
            except (ValueError, KeyError):
                pass
            log.exception('Suggestion processing failed; SQS will retry, then redrive to DLQ')
    return {'processed': succeeded, 'failed': failed}

def handler(event, context):
    if os.environ.get('WORKER_LOCK_ENABLED') != 'true':
        return poll(event, context)
    state = table(os.environ['STATE_TABLE'])
    owner = getattr(context, 'aws_request_id', None) or str(uuid.uuid4())
    now = int(time.time())
    key = {'pk': 'WORKER#LF2'}
    try:
        state.update_item(Key=key,
            UpdateExpression='SET lockOwner = :owner, expiresAt = :expires',
            ConditionExpression='attribute_not_exists(expiresAt) OR expiresAt < :now',
            ExpressionAttributeValues={':owner': owner, ':expires': now + 150, ':now': now})
    except ClientError as exc:
        if exc.response['Error']['Code'] == 'ConditionalCheckFailedException':
            return {'processed': 0, 'failed': 0, 'skipped': 'another worker is active'}
        raise
    try:
        return poll(event, context)
    finally:
        try:
            state.delete_item(Key=key, ConditionExpression='lockOwner = :owner',
                              ExpressionAttributeValues={':owner': owner})
        except ClientError as exc:
            if exc.response['Error']['Code'] != 'ConditionalCheckFailedException':
                raise
