"""Deterministic protocol peer for transport tests; not an integration test."""
import json
import sys

for line in sys.stdin:
    message = json.loads(line)
    if 'id' not in message:
        continue
    method = message['method']
    if method == 'hang':
        continue
    if method == 'exit':
        break
    result = {'ok': True}
    if method == 'reject':
        print(json.dumps({'id': message['id'], 'error': {'code': -1, 'message': 'private upstream detail'}}), flush=True)
        continue
    print(json.dumps({'id': message['id'], 'result': result}), flush=True)
    if method == 'event':
        print(json.dumps({'method': 'test/event', 'params': {}}), flush=True)
