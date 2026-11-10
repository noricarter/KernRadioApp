"""Offline App Server peer exercising real client/server request dispatch."""
import json
import sys


def send(message):
    print(json.dumps(message), flush=True)


for line in sys.stdin:
    message = json.loads(line)
    method = message.get('method')
    if method == 'initialize':
        send({'id': message['id'], 'result': {}})
    elif method == 'thread/start':
        send({'id': message['id'], 'result': {'thread': {'id': 'thread'}}})
    elif method == 'turn/start':
        send({'id': message['id'], 'result': {'turn': {'id': 'turn'}}})
        send({'id': 'server-call', 'method': 'item/tool/call', 'params': {
            'threadId': 'thread', 'turnId': 'turn', 'callId': 'call',
            'tool': sys.argv[1], 'arguments': {'value': 'actual argument'}}})
    elif method is None and message.get('id') == 'server-call':
        result = message.get('result', message.get('error'))
        send({'method': 'item/completed', 'params': {'threadId': 'thread', 'turnId': 'turn',
            'item': {'type': 'agentMessage', 'id': 'reply', 'text': json.dumps(result)}}})
        send({'method': 'turn/completed', 'params': {'threadId': 'thread', 'turn': {'id': 'turn', 'status': 'completed'}}})
