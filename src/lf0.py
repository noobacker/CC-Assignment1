"""API Gateway /chatbot -> Lex V2, preserving the assignment's Swagger envelope."""
import base64
import json
import logging
import os
import re
import uuid
from common import client, now_iso

log = logging.getLogger(__name__)

def response(status, body, session_id=None):
    headers = {'Content-Type': 'application/json',
               'Access-Control-Allow-Origin': os.environ.get('ALLOWED_ORIGIN', '*'),
               'Access-Control-Allow-Headers': 'Content-Type,X-Session-Id',
               'Access-Control-Allow-Methods': 'POST,OPTIONS',
               'Access-Control-Expose-Headers': 'X-Session-Id', 'Cache-Control': 'no-store'}
    if session_id:
        headers['X-Session-Id'] = session_id
    return {'statusCode': status, 'headers': headers, 'body': json.dumps(body)}

def handler(event, context):
    if event.get('httpMethod') == 'OPTIONS':
        return response(200, {})
    try:
        raw = event.get('body', '{}')
        if event.get('isBase64Encoded'):
            raw = base64.b64decode(raw).decode()
        body = json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(body, dict):
            raise ValueError('Request must be a JSON object.')
        messages = body.get('messages')
        if not isinstance(messages, list) or not 1 <= len(messages) <= 5:
            raise ValueError('Provide between 1 and 5 messages.')
        texts = []
        for message in messages:
            if not isinstance(message, dict) or message.get('type') != 'unstructured':
                raise ValueError('Each message must have type unstructured.')
            unstructured = message.get('unstructured')
            text = unstructured.get('text') if isinstance(unstructured, dict) else None
            if not isinstance(text, str) or not 1 <= len(text.strip()) <= 1024:
                raise ValueError('Each text must contain 1 to 1024 characters.')
            texts.append(text.strip())
        headers = {k.lower(): v for k, v in (event.get('headers') or {}).items()}
        session_id = headers.get('x-session-id') or str(uuid.uuid4())
        if not re.fullmatch(r'[A-Za-z0-9_.:-]{2,100}', session_id):
            raise ValueError('Invalid X-Session-Id.')
    except (ValueError, TypeError, UnicodeError) as exc:
        return response(400, {'code': 400, 'message': str(exc)})
    try:
        replies = []
        for text in texts:
            if os.environ.get('BOILERPLATE_MODE', 'false').lower() == 'true':
                outputs = [{'content': "I'm still under development. Please come back later."}]
            else:
                result = client('lexv2-runtime').recognize_text(
                    botId=os.environ['LEX_BOT_ID'], botAliasId=os.environ['LEX_ALIAS_ID'],
                    localeId='en_US', sessionId=session_id, text=text)
                outputs = result.get('messages') or [{'content': 'Please tell me your dining preferences.'}]
            replies.extend({'type': 'unstructured', 'unstructured': {
                'id': str(uuid.uuid4()), 'text': m['content'], 'timestamp': now_iso()}}
                for m in outputs if m.get('content'))
        return response(200, {'messages': replies}, session_id)
    except Exception:
        log.exception('Chat backend failed')
        return response(502, {'code': 502, 'message': 'The concierge is temporarily unavailable. Please retry.'}, session_id)
