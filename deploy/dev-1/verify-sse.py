"""Validate real unconfigured-model error transport; never simulates successful questions."""
import importlib.util
import json
import urllib.request
from pathlib import Path
spec = importlib.util.spec_from_file_location('api', Path(__file__).with_name('verify-api.py'))
api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api)
token = api.login('sqlbot1')
base = 'http://127.0.0.1:20170'
status, result = api.request(base, '/api/v1/chat/start', token, {})
assert status == 200, (status, result)
chat = result.get('data', result)
request = urllib.request.Request(base + '/api/v1/chat/question',
    data=json.dumps({'chat_id': chat['id'], 'question': '本次部署验证：虚构订单总金额是多少？'}).encode(),
    headers={'Content-Type': 'application/json', 'Authorization': 'Bearer '+token, 'tenant-id': '124'})
with urllib.request.urlopen(request, timeout=90) as response:
    content_type = response.headers.get('Content-Type', '')
    data = response.read().decode()
assert 'text/event-stream' in content_type, content_type
assert 'data:' in data and 'error' in data, data
api.A.joinpath('sse-no-model-acceptance.json').write_text(json.dumps({'contentType': content_type, 'body': data}, ensure_ascii=False, indent=2))
print('REAL_SSE_ERROR_TRANSPORT_OK model_not_configured')
