"""Render a clearly synthetic fixture and verify the deployed image proxy."""
import json
import urllib.request
from pathlib import Path

body = {'type': 'pie', 'path': '/opt/sqlbot/images/jt-deployment-probe.png',
        'axis': json.dumps([{'name': '类型', 'value': 'name', 'type': 'series'}, {'name': '金额', 'value': 'amount', 'type': 'y'}]),
        'data': json.dumps([{'name': '测试甲', 'amount': 100}, {'name': '测试乙', 'amount': 200}])}
request = urllib.request.Request('http://127.0.0.1:20189', data=json.dumps(body).encode(), headers={'Content-Type': 'application/json'})
with urllib.request.urlopen(request, timeout=60) as response:
    assert response.read() == b'complete'
with urllib.request.urlopen('http://127.0.0.1:20170/images/jt-deployment-probe.png', timeout=10) as response:
    image = response.read()
assert image.startswith(b'\x89PNG\r\n\x1a\n') and len(image) > 1000
Path('/data/code/jt/deployment-audit/20260921-sqlbot-integration/chart-probe.png').write_bytes(image)
print('CHART_RENDER_AND_IMAGE_PROXY_OK', len(image))
