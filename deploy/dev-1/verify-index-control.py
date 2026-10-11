"""Exercise owned Data index control; keep scheduling disabled throughout."""
import importlib.util
import json
import time
from pathlib import Path

spec = importlib.util.spec_from_file_location('api', Path(__file__).with_name('verify-api.py'))
api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api)
token = api.login('sqlbot3')
base = 'http://172.17.0.1:20180'
path = '/admin-api/dmgt/data-source/index-sync'
def call(suffix='', body=None, method=None):
    status, result = api.request(base, path + suffix, token, body, method=method)
    assert result.get('code') == 0, (status, result)
    return result['data']
before = call()
assert not before['enabled']
try:
    changed = call('/schedule', {'enabled': False, 'time': '21:59', 'force': False}, 'PUT')
    assert changed['time'] == '21:59' and not changed['enabled']
    assert call()['time'] == '21:59'
    batch = call('/all', {'force': False})['batch']
    duplicate = call('/all', {'force': False})['batch']
    assert duplicate['id'] == batch['id'], 'Active batch was not deduplicated'
    for _ in range(120):
        current = call()
        if current['batch']['status'] not in ['pending', 'running']:
            break
        time.sleep(2)
    assert current['batch']['status'] == 'succeeded', current
    assert len(current['batch']['items']) == 2, current
    api.A.joinpath('index-control-acceptance.json').write_text(json.dumps(current, indent=2))
finally:
    restored = call('/schedule', {'enabled': False, 'time': before['time'], 'force': before['force']}, 'PUT')
    assert not restored['enabled']
print('INDEX_CONTROL_CONFIG_DEDUP_BATCH_OK schedule=disabled')
