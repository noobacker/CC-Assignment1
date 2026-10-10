"""Lex V2 dialog validation, fulfillment, and previous-recommendation consent."""
import hashlib
import json
import os
import re
from datetime import datetime, date, time, timedelta
from zoneinfo import ZoneInfo
from common import CUISINES, client, table, now_iso

PROMPTS = {
    'Location': 'What city or city area would you like to dine in? I serve Manhattan.',
    'Cuisine': 'What cuisine would you like? Chinese, Japanese, Italian, Mexican, Indian, Thai, French, or American?',
    'NumberOfPeople': 'How many people are in your party (1 to 20)?',
    'DiningDate': 'What date would you like to dine?',
    'DiningTime': 'What time would you like to dine (New York time)?',
    'Email': 'What email address should receive your restaurant suggestions?'}

def value(slots, name):
    return ((slots.get(name) or {}).get('value') or {}).get('interpretedValue')

def set_value(slots, name, val):
    slots[name] = {'shape': 'Scalar', 'value': {'originalValue': val,
                   'interpretedValue': val, 'resolvedValues': [val]}}

def reply(event, action, text=None, slot=None, state='InProgress'):
    intent = dict(event['sessionState']['intent'])
    intent['state'] = state
    dialog = {'type': action}
    if slot:
        dialog['slotToElicit'] = slot
    result = {'sessionState': {'dialogAction': dialog, 'intent': intent,
              'sessionAttributes': event['sessionState'].get('sessionAttributes', {})}}
    if text:
        result['messages'] = [{'contentType': 'PlainText', 'content': text}]
    return result

def validate(slots, now=None):
    now = now or datetime.now(ZoneInfo('America/New_York'))
    location = value(slots, 'Location')
    if location:
        if location.strip().lower() not in {'manhattan', 'new york', 'new york city', 'nyc', 'new york, ny'}:
            return 'Location', 'Sorry, I can only suggest restaurants in Manhattan. Please enter Manhattan.'
        set_value(slots, 'Location', 'Manhattan')
    cuisine = value(slots, 'Cuisine')
    if cuisine:
        cuisine = cuisine.strip().lower()
        if cuisine not in CUISINES:
            return 'Cuisine', PROMPTS['Cuisine']
        set_value(slots, 'Cuisine', cuisine)
    people = value(slots, 'NumberOfPeople')
    if people and (not re.fullmatch(r'\d+', people) or not 1 <= int(people) <= 20):
        return 'NumberOfPeople', PROMPTS['NumberOfPeople']
    dining_date = value(slots, 'DiningDate')
    if dining_date:
        try:
            day = date.fromisoformat(dining_date)
            if day < now.date() or day > now.date() + timedelta(days=365):
                raise ValueError()
        except ValueError:
            return 'DiningDate', 'Please choose a date from today through the next year.'
    dining_time = value(slots, 'DiningTime')
    if dining_time:
        try:
            if not re.fullmatch(r'\d{2}:\d{2}', dining_time):
                raise ValueError()
            clock = time.fromisoformat(dining_time)
            if dining_date and datetime.combine(date.fromisoformat(dining_date), clock, now.tzinfo) <= now:
                raise ValueError()
        except ValueError:
            return 'DiningTime', 'Please choose a future dining time, such as 7 pm (New York time).'
    email = value(slots, 'Email')
    if email and (len(email) > 254 or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email)):
        return 'Email', 'Please enter a valid email address, such as name@example.com.'
    return None

def handler(event, context):
    intent = event['sessionState']['intent']
    name = intent['name']
    if name == 'GreetingIntent':
        return reply(event, 'Close', 'Hi there, how can I help? I can suggest restaurants in Manhattan.', state='Fulfilled')
    if name == 'ThankYouIntent':
        return reply(event, 'Close', "You're welcome. Enjoy your meal!", state='Fulfilled')
    if name != 'DiningSuggestionsIntent':
        return reply(event, 'Close', 'I can help with restaurant suggestions. Say: I need restaurant suggestions.', state='Fulfilled')
    slots = intent.setdefault('slots', {})
    error = validate(slots)
    if error:
        slot, message = error
        slots[slot] = None
        slots['ReusePrevious'] = None
        return reply(event, 'ElicitSlot', message, slot)
    for slot in ('Location', 'Cuisine'):
        if not value(slots, slot):
            return reply(event, 'ElicitSlot', PROMPTS[slot], slot)
    user_id = event['sessionId']
    state_table = table(os.environ['STATE_TABLE'])
    previous = state_table.get_item(Key={'pk': 'USER#' + user_id}, ConsistentRead=True).get('Item', {})
    matches = (previous.get('location') == value(slots, 'Location') and
               previous.get('cuisine') == value(slots, 'Cuisine') and previous.get('restaurantIds'))
    reuse = (value(slots, 'ReusePrevious') or '').lower()
    if matches and reuse not in {'yes', 'no'}:
        slots['ReusePrevious'] = None
        return reply(event, 'ElicitSlot',
                     'You previously searched for this location and cuisine. Would you like the same recommendations as last time? Say yes or no.',
                     'ReusePrevious')
    for slot, prompt in PROMPTS.items():
        if not value(slots, slot):
            return reply(event, 'ElicitSlot', prompt, slot)
    # Complete through Lex's fulfillment phase so its lifecycle stays consistent.
    if event.get('invocationSource') == 'DialogCodeHook':
        return reply(event, 'Delegate')
    request = {'userId': user_id, 'location': value(slots, 'Location'),
               'cuisine': value(slots, 'Cuisine'), 'numberOfPeople': int(value(slots, 'NumberOfPeople')),
               'diningDate': value(slots, 'DiningDate'), 'diningTime': value(slots, 'DiningTime'),
               'email': value(slots, 'Email'), 'requestedAt': now_iso()}
    if matches and reuse == 'yes':
        request['restaurantIds'] = previous['restaurantIds']
    stable = {k: v for k, v in request.items() if k != 'requestedAt'}
    stable['turn'] = event['sessionState'].get('originatingRequestId', event.get('inputTranscript', ''))
    request['requestId'] = hashlib.sha256(json.dumps(stable, sort_keys=True).encode()).hexdigest()
    client('sqs').send_message(QueueUrl=os.environ['QUEUE_URL'], MessageBody=json.dumps(request),
                              MessageGroupId=user_id, MessageDeduplicationId=request['requestId'])
    return reply(event, 'Close', "You're all set. I received your request and will email your restaurant suggestions shortly. Have a good day!", state='Fulfilled')
