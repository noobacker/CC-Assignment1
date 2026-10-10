from unittest.mock import Mock
import pytest
from botocore.exceptions import ClientError
import lf2

@pytest.mark.parametrize('failure', [False, True])
def test_worker_releases_lock_even_on_poll_failure(monkeypatch, failure):
    monkeypatch.setenv('WORKER_LOCK_ENABLED','true')
    state=Mock()
    monkeypatch.setattr(lf2,'table',lambda _:state)
    poll=Mock(side_effect=RuntimeError('AWS failed') if failure else None, return_value={'processed':0,'failed':0})
    monkeypatch.setattr(lf2,'poll',poll)
    if failure:
        with pytest.raises(RuntimeError): lf2.handler({},None)
    else:
        assert lf2.handler({},None)=={'processed':0,'failed':0}
    acquired=state.update_item.call_args.kwargs
    released=state.delete_item.call_args.kwargs
    assert acquired['ExpressionAttributeValues'][':owner']==released['ExpressionAttributeValues'][':owner']
    assert acquired['ExpressionAttributeValues'][':expires']-acquired['ExpressionAttributeValues'][':now']==150

def test_overlapping_worker_does_not_poll_or_release_others_lock(monkeypatch):
    monkeypatch.setenv('WORKER_LOCK_ENABLED','true')
    state=Mock()
    state.update_item.side_effect=ClientError({'Error':{'Code':'ConditionalCheckFailedException','Message':'held'}},'UpdateItem')
    monkeypatch.setattr(lf2,'table',lambda _:state)
    poll=Mock()
    monkeypatch.setattr(lf2,'poll',poll)
    assert 'skipped' in lf2.handler({},None)
    poll.assert_not_called()
    state.delete_item.assert_not_called()
