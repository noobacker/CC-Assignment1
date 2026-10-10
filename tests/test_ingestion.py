import json
from unittest.mock import Mock
import ingest_yelp
from test_application import yelp_business


def test_collection_respects_search_cap_and_deduplicates_cuisines(monkeypatch, tmp_path):
    monkeypatch.setenv('YELP_API_KEY', 'test-only-not-a-real-key')
    monkeypatch.setattr(ingest_yelp, 'CUISINES', ['japanese', 'chinese'])
    monkeypatch.setattr(ingest_yelp.time, 'sleep', lambda _: None)
    calls = []
    def fetch(key, params):
        calls.append(params)
        assert params['offset'] + params['limit'] <= 240
        rows = []
        for index in range(params['offset'], params['offset'] + params['limit']):
            row = yelp_business()
            row['id'] = f'id-{index}'
            rows.append(row)
        return {'businesses': rows, 'total': 5000}
    monkeypatch.setattr(ingest_yelp, 'fetch', fetch)
    path = tmp_path / 'restaurants.json'
    rows = ingest_yelp.collect(path, 200)
    assert len(rows) == 200
    assert all(set(row['Cuisine']) == {'japanese', 'chinese'} for row in rows)
    count = len(calls)
    assert ingest_yelp.collect(path, 200) == rows
    assert len(calls) == count  # Resume does not re-fetch complete cuisines.


def test_import_typed_mapping_partial_index_and_dynamo_fields(monkeypatch):
    search = Mock(return_value={'errors': False})
    monkeypatch.setattr(ingest_yelp, 'search_request', search)
    batch = Mock()
    db = Mock()
    db.batch_writer.return_value.__enter__ = Mock(return_value=batch)
    db.batch_writer.return_value.__exit__ = Mock(return_value=False)
    resource = Mock(); resource.Table.return_value = db
    monkeypatch.setattr(ingest_yelp.boto3, 'resource', lambda _: resource)
    row = ingest_yelp.normalize(yelp_business(), 'japanese')
    ingest_yelp.load([row], {'SearchEngineVersion': 'Elasticsearch_7.10', 'RestaurantsTable': 'yelp-restaurants'})
    mapping_call, bulk_call, refresh_call = search.call_args_list
    assert mapping_call.args[1] == '/restaurants?include_type_name=true'
    assert 'Restaurant' in mapping_call.args[2]['mappings']
    metadata, body = map(json.loads, bulk_call.args[2].strip().split('\n'))
    assert metadata['index']['_type'] == 'Restaurant'
    assert body == {'RestaurantID': row['BusinessID'], 'Cuisine': ['japanese']}
    item = batch.put_item.call_args.kwargs['Item']
    assert item['Name'] == row['Name'] and item['insertedAtTimestamp']
