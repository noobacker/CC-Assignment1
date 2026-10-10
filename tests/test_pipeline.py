"""Cross-Lambda integration with in-memory AWS transports (no live service claims)."""
from copy import deepcopy
import json
from unittest.mock import Mock
import lf0
import lf1
import lf2
from test_application import lex_event, restaurant, request_body

class MemoryTable:
    def __init__(self): self.items = {}
    def get_item(self, Key, **kwargs):
        item = self.items.get(tuple(Key.values()))
        return {'Item': deepcopy(item)} if item else {}
    def put_item(self, Item):
        self.items[(Item.get('pk') or Item['BusinessID'],)] = deepcopy(Item)

class Queue:
    def __init__(self): self.messages = []
    def send_message(self, **kwargs):
        self.messages.append({'Body': kwargs['MessageBody'], 'ReceiptHandle': str(len(self.messages))})
    def receive_message(self, **kwargs): return {'Messages': deepcopy(self.messages)}
    def delete_message(self, ReceiptHandle, **kwargs):
        self.messages = [m for m in self.messages if m['ReceiptHandle'] != ReceiptHandle]

def test_api_hook_queue_worker_and_repeat(monkeypatch):
    state, restaurants, queue, ses = MemoryTable(), MemoryTable(), Queue(), Mock()
    ses.send_email.return_value = {'MessageId': 'ses-test'}
    for name in ['one', 'two', 'three']: restaurants.put_item(Item=restaurant(name))
    monkeypatch.setattr(lf1, 'table', lambda _: state)
    monkeypatch.setattr(lf1, 'client', lambda _: queue)
    monkeypatch.setattr(lf2, 'table', lambda name: state if name == 'test-state' else restaurants)
    monkeypatch.setattr(lf2, 'client', lambda service: queue if service == 'sqs' else ses)
    search = Mock(return_value={'hits': {'hits': [{'_source': {'RestaurantID': name}} for name in ['one','two','three']]}})
    monkeypatch.setattr(lf2, 'search_request', search)
    event = lex_event()
    lex = Mock()
    def recognize(**kwargs):
        event['sessionId'] = kwargs['sessionId']
        reply = lf1.handler(deepcopy(event), None)
        return {'messages': reply.get('messages', [])}
    lex.recognize_text.side_effect = recognize
    monkeypatch.setattr(lf0, 'client', lambda _: lex)
    request = {'body': json.dumps(request_body('guest@example.com')), 'headers': {'X-Session-Id': 'integration-user'}}
    result = lf0.handler(request, None)
    assert result['statusCode'] == 200 and len(queue.messages) == 1
    assert lf2.handler({}, None) == {'processed': 1, 'failed': 0}
    assert queue.messages == [] and ses.send_email.call_count == 1
    history = state.get_item(Key={'pk': 'USER#integration-user'})['Item']
    assert history['restaurantIds'] == ['one', 'two', 'three']
    # Same browser returns, even after Lex's transient session has expired.
    event['sessionState']['originatingRequestId'] = 'return-turn'
    result = lf0.handler(request, None)
    assert 'same recommendations' in result['body'] and queue.messages == []
    lf1.set_value(event['sessionState']['intent']['slots'], 'ReusePrevious', 'yes')
    lf0.handler(request, None)
    assert lf2.handler({}, None)['processed'] == 1
    assert ses.send_email.call_count == 2 and search.call_count == 1
    assert state.get_item(Key={'pk': 'USER#integration-user'})['Item']['restaurantIds'] == history['restaurantIds']
