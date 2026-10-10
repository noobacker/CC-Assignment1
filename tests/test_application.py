import base64
from copy import deepcopy
from datetime import datetime, timedelta
import json
from unittest.mock import Mock
from zoneinfo import ZoneInfo
import pytest
import lf0
import lf1
import lf2
from ingest_yelp import normalize, coverage
from build_template import build

NOW = datetime(2026, 10, 7, 15, 0, tzinfo=ZoneInfo('America/New_York'))

def slot_values(**updates):
    values = {'Location': 'Manhattan', 'Cuisine': 'Japanese', 'NumberOfPeople': '2',
              'DiningDate': (datetime.now(ZoneInfo('America/New_York')) + timedelta(days=1)).date().isoformat(),
              'DiningTime': '19:00', 'Email': 'guest@example.com'}
    values.update(updates)
    slots = {}
    for name, value in values.items():
        if value is not None: lf1.set_value(slots, name, value)
        else: slots[name] = None
    return slots

def lex_event(name='DiningSuggestionsIntent', source='FulfillmentCodeHook', **slots):
    return {'sessionId': 'user-123', 'invocationSource': source, 'inputTranscript': 'test',
            'sessionState': {'intent': {'name': name, 'slots': slot_values(**slots)},
                             'sessionAttributes': {}, 'originatingRequestId': 'turn-123'}}

@pytest.fixture
def state(monkeypatch):
    state = Mock()
    state.get_item.return_value = {}
    monkeypatch.setattr(lf1, 'table', lambda _: state)
    return state

@pytest.fixture
def sqs(monkeypatch):
    sqs = Mock()
    monkeypatch.setattr(lf1, 'client', lambda _: sqs)
    return sqs

@pytest.mark.parametrize('name,text', [('GreetingIntent', 'help'), ('ThankYouIntent', 'welcome'), ('FallbackIntent', 'suggestions')])
def test_smalltalk(name, text):
    result = lf1.handler(lex_event(name), None)
    assert text in result['messages'][0]['content']
    assert result['sessionState']['intent']['state'] == 'Fulfilled'

@pytest.mark.parametrize('updates,expected', [
    ({'Location': 'New Delhi'}, 'Location'), ({'Cuisine': 'martian'}, 'Cuisine'),
    ({'NumberOfPeople': '0'}, 'NumberOfPeople'), ({'NumberOfPeople': '21'}, 'NumberOfPeople'),
    ({'NumberOfPeople': '2.5'}, 'NumberOfPeople'), ({'Email': 'bad@'}, 'Email'),
    ({'DiningDate': '2020-01-01'}, 'DiningDate'), ({'DiningDate': '2026-W41'}, 'DiningDate'),
    ({'DiningTime': '25:00'}, 'DiningTime'), ({'DiningTime': 'EV'}, 'DiningTime'),
    ({'DiningDate': '2026-10-07', 'DiningTime': '14:00'}, 'DiningTime')])
def test_validation(updates, expected):
    slots = slot_values(DiningDate='2026-10-08', **{k:v for k,v in updates.items() if k != 'DiningDate'})
    if 'DiningDate' in updates: lf1.set_value(slots, 'DiningDate', updates['DiningDate'])
    assert lf1.validate(slots, NOW)[0] == expected

def test_valid_slots_are_normalized():
    slots = slot_values(Location='NYC', Cuisine=' JAPANESE ', DiningDate='2026-10-08')
    assert lf1.validate(slots, NOW) is None
    assert lf1.value(slots, 'Location') == 'Manhattan'
    assert lf1.value(slots, 'Cuisine') == 'japanese'

def test_bad_slot_is_cleared_before_eliciting():
    result = lf1.handler(lex_event(Location='Delhi'), None)
    assert result['sessionState']['dialogAction']['slotToElicit'] == 'Location'
    assert result['sessionState']['intent']['slots']['Location'] is None

def test_missing_slot_is_requested(state):
    result = lf1.handler(lex_event(Email=None), None)
    assert result['sessionState']['dialogAction']['slotToElicit'] == 'Email'

def test_dialog_delegates_and_fulfillment_queues(state, sqs):
    dialog = lf1.handler(lex_event(source='DialogCodeHook'), None)
    assert dialog['sessionState']['dialogAction']['type'] == 'Delegate'
    sqs.send_message.assert_not_called()
    result = lf1.handler(lex_event(), None)
    message = json.loads(sqs.send_message.call_args.kwargs['MessageBody'])
    assert message['numberOfPeople'] == 2 and message['cuisine'] == 'japanese'
    assert message['email'] == 'guest@example.com'
    assert result['sessionState']['intent']['state'] == 'Fulfilled'
    assert 'email' in result['messages'][0]['content']

@pytest.mark.parametrize('answer,reused', [('yes', True), ('no', False)])
def test_repeat_choice_preserves_or_replaces_ids(state, sqs, answer, reused):
    state.get_item.return_value = {'Item': {'location': 'Manhattan', 'cuisine': 'japanese', 'restaurantIds': ['old-id']}}
    asked = lf1.handler(lex_event(), None)
    assert asked['sessionState']['dialogAction']['slotToElicit'] == 'ReusePrevious'
    sqs.send_message.assert_not_called()
    lf1.handler(lex_event(ReusePrevious=answer), None)
    message = json.loads(sqs.send_message.call_args.kwargs['MessageBody'])
    assert ('restaurantIds' in message) == reused
    if reused: assert message['restaurantIds'] == ['old-id']

def test_different_cuisine_does_not_reuse(state, sqs):
    state.get_item.return_value = {'Item': {'location': 'Manhattan', 'cuisine': 'italian', 'restaurantIds': ['old-id']}}
    assert lf1.handler(lex_event(), None)['sessionState']['intent']['state'] == 'Fulfilled'

def test_repeat_prompt_precedes_party_date_time_and_email(state, sqs):
    state.get_item.return_value = {'Item': {'location': 'Manhattan', 'cuisine': 'japanese', 'restaurantIds': ['old-id']}}
    event = lex_event(source='DialogCodeHook', NumberOfPeople=None, DiningDate=None, DiningTime=None, Email=None)
    result = lf1.handler(event, None)
    assert result['sessionState']['dialogAction']['slotToElicit'] == 'ReusePrevious'
    sqs.send_message.assert_not_called()
    lf1.set_value(event['sessionState']['intent']['slots'], 'ReusePrevious', 'yes')
    result = lf1.handler(event, None)
    assert result['sessionState']['dialogAction']['slotToElicit'] == 'NumberOfPeople'
    sqs.send_message.assert_not_called()

def test_queue_failure_does_not_claim_success(state, sqs):
    sqs.send_message.side_effect = RuntimeError('queue unavailable')
    with pytest.raises(RuntimeError): lf1.handler(lex_event(), None)

def request_body(text='hello'):
    return {'messages': [{'type': 'unstructured', 'unstructured': {'text': text}}]}

def test_lf0_lex_contract_and_session(monkeypatch):
    lex = Mock()
    lex.recognize_text.return_value = {'messages': [{'content': 'Hi there', 'contentType': 'PlainText'}]}
    monkeypatch.setattr(lf0, 'client', lambda _: lex)
    result = lf0.handler({'body': json.dumps(request_body()), 'headers': {'X-Session-Id': 'same-browser'}}, None)
    assert result['statusCode'] == 200
    assert lex.recognize_text.call_args.kwargs['sessionId'] == 'same-browser'
    assert json.loads(result['body'])['messages'][0]['unstructured']['text'] == 'Hi there'
    assert result['headers']['Access-Control-Expose-Headers'] == 'X-Session-Id'

@pytest.mark.parametrize('body', ['{bad', 'null', '[]', '{}', '{"messages":[null]}',
    '{"messages":[{"type":"unstructured","unstructured":null}]}', json.dumps(request_body(' ')),
    json.dumps(request_body('x' * 1025))])
def test_invalid_api_requests(body):
    assert lf0.handler({'body': body}, None)['statusCode'] == 400

def test_invalid_session_id():
    assert lf0.handler({'body': json.dumps(request_body()), 'headers': {'X-Session-Id': 'bad space'}}, None)['statusCode'] == 400

def test_base64_and_boilerplate(monkeypatch):
    monkeypatch.setenv('BOILERPLATE_MODE', 'true')
    result = lf0.handler({'body': base64.b64encode(json.dumps(request_body()).encode()).decode(), 'isBase64Encoded': True}, None)
    assert result['statusCode'] == 200 and 'under development' in result['body']

def test_preflight():
    assert lf0.handler({'httpMethod': 'OPTIONS'}, None)['statusCode'] == 200

def test_lex_failure_is_sanitized(monkeypatch):
    lex = Mock()
    lex.recognize_text.side_effect = RuntimeError('private internal detail')
    monkeypatch.setattr(lf0, 'client', lambda _: lex)
    result = lf0.handler({'body': json.dumps(request_body())}, None)
    assert result['statusCode'] == 502 and 'private internal detail' not in result['body']

def dining_request():
    return {'requestId': 'r1', 'userId': 'u1', 'cuisine': 'japanese', 'location': 'Manhattan',
            'numberOfPeople': 2, 'diningDate': '2026-10-08', 'diningTime': '19:00', 'email': 'guest@example.com'}

def restaurant(identifier='r1'):
    return {'BusinessID': identifier, 'Name': 'Test Restaurant', 'Address': '1 Test St, New York, NY 10001',
            'Rating': 4.5, 'NumberOfReviews': 100}

def test_worker_joins_search_ids_to_dynamo(monkeypatch):
    search = Mock(return_value={'hits': {'hits': [{'_source': {'RestaurantID': 'r1'}}, {'_source': {'RestaurantID': 'r1'}}]}})
    db = Mock(); db.get_item.return_value = {'Item': restaurant()}
    monkeypatch.setattr(lf2, 'search_request', search)
    monkeypatch.setattr(lf2, 'table', lambda _: db)
    result = lf2.recommendations(dining_request())
    assert len(result) == 1
    assert search.call_args.args[2]['query']['function_score']['query'] == {'term': {'Cuisine': 'japanese'}}
    assert db.get_item.call_args.kwargs['Key'] == {'BusinessID': 'r1'}

def test_repeat_bypasses_random_search(monkeypatch):
    search = Mock()
    db = Mock(); db.get_item.return_value = {'Item': restaurant()}
    monkeypatch.setattr(lf2, 'search_request', search)
    monkeypatch.setattr(lf2, 'table', lambda _: db)
    lf2.recommendations(dict(dining_request(), restaurantIds=['r1']))
    search.assert_not_called()

def test_no_data_is_failure(monkeypatch):
    monkeypatch.setattr(lf2, 'search_request', lambda *a: {'hits': {'hits': []}})
    with pytest.raises(RuntimeError, match='No restaurants'): lf2.recommendations(dining_request())

def test_email_has_preferences_and_details():
    text = lf2.email_text(dining_request(), [restaurant()])
    for expected in ['Japanese', '2 people', '2026-10-08', '19:00', 'Test Restaurant', '1 Test St']:
        assert expected in text

def test_processed_message_not_sent_twice(monkeypatch):
    db = Mock(); db.get_item.return_value = {'Item': {'status': 'SENT'}}
    ses = Mock()
    monkeypatch.setattr(lf2, 'table', lambda _: db)
    monkeypatch.setattr(lf2, 'client', lambda _: ses)
    lf2.process(dining_request())
    ses.send_email.assert_not_called()

@pytest.mark.parametrize('fails', [False, True])
def test_delete_only_after_success(monkeypatch, fails):
    sqs = Mock(); sqs.receive_message.return_value = {'Messages': [{'Body': json.dumps(dining_request()), 'ReceiptHandle': 'receipt'}]}
    process = Mock(side_effect=RuntimeError('SES unavailable') if fails else None)
    monkeypatch.setattr(lf2, 'client', lambda _: sqs)
    monkeypatch.setattr(lf2, 'process', process)
    result = lf2.handler({}, None)
    assert result['failed'] == int(fails)
    assert sqs.delete_message.call_count == int(not fails)

def test_ses_success_persists_history(monkeypatch):
    db = Mock(); db.get_item.return_value = {}
    ses = Mock(); ses.send_email.return_value = {'MessageId': 'ses-1'}
    monkeypatch.setattr(lf2, 'table', lambda _: db)
    monkeypatch.setattr(lf2, 'client', lambda _: ses)
    monkeypatch.setattr(lf2, 'recommendations', lambda _: [restaurant()])
    lf2.process(dining_request())
    items = [call.kwargs['Item'] for call in db.put_item.call_args_list]
    assert items[0]['status'] == 'PENDING'
    assert items[1]['pk'] == 'USER#u1' and items[1]['restaurantIds'] == ['r1']
    assert items[2]['status'] == 'SENT'
    assert ses.send_email.call_args.kwargs['Destination']['ToAddresses'] == ['guest@example.com']

def yelp_business():
    return {'id': 'real-yelp-id', 'name': 'Name', 'location': {'state': 'NY', 'zip_code': '10001',
        'address1': '1 Broadway', 'display_address': ['1 Broadway', 'New York, NY 10001']},
        'coordinates': {'latitude': 40.7, 'longitude': -74.0}, 'rating': 4.0, 'review_count': 30, 'is_closed': False}

def test_yelp_normalization():
    row = normalize(yelp_business(), 'japanese', '2026-10-07T00:00:00Z')
    assert row['BusinessID'] == 'real-yelp-id' and row['insertedAtTimestamp'] == '2026-10-07T00:00:00Z'
    assert row['Coordinates']['latitude'] == 40.7

@pytest.mark.parametrize('change', ['outside', 'closed', 'missing-coordinate', 'missing-address'])
def test_yelp_rejects_ineligible_records(change):
    business = yelp_business()
    if change == 'outside': business['location']['zip_code'] = '11201'
    if change == 'closed': business['is_closed'] = True
    if change == 'missing-coordinate': business['coordinates']['latitude'] = None
    if change == 'missing-address': business['location']['address1'] = ''
    assert normalize(business, 'japanese') is None

def test_dataset_threshold():
    assert not coverage([{'Cuisine': ['japanese']} for _ in range(999)])['meetsRequirement']
    assert coverage([{'Cuisine': ['chinese','japanese','italian','mexican','american'][i % 5:i % 5+1]} for i in range(1000)])['meetsRequirement']

def test_infrastructure_requirements():
    r = build()['Resources']
    assert r['Restaurants']['Properties']['TableName'] == 'yelp-restaurants'
    assert r['MinuteSchedule']['Properties']['ScheduleExpression'] == 'rate(1 minute)'
    assert r['LF2']['Properties']['Environment']['Variables']['WORKER_LOCK_ENABLED'] == 'true'
    intents = r['Bot']['Properties']['BotLocales'][0]['Intents']
    assert {'GreetingIntent', 'ThankYouIntent', 'DiningSuggestionsIntent'} <= {i['Name'] for i in intents}
    assert r['Search']['Properties']['ClusterConfig']['InstanceCount'] == 1
    assert r['Q1']['Properties']['RedrivePolicy']['maxReceiveCount'] == 5
    assert 'options' in r['Api']['Properties']['Body']['paths']['/chatbot']
