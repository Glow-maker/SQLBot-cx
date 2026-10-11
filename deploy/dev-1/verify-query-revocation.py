"""Invoke around the authorized sqlbot4 grant toggle; this script never changes grants."""
import argparse
import importlib.util
import json
from pathlib import Path

spec = importlib.util.spec_from_file_location('security', Path(__file__).with_name('verify-query-security.py'))
security = importlib.util.module_from_spec(spec)
spec.loader.exec_module(security)
real = security.real

parser = argparse.ArgumentParser()
parser.add_argument('state', choices=['before', 'revoked', 'restored'])
args = parser.parse_args()
private = real.A/'revocation-token.private'
if args.state == 'before':
    token = real.login('sqlbot4')
    private.write_text(token)
    private.chmod(0o600)
else:
    token = private.read_text()
record = next(d for d in map(json.loads, (real.A/'real-questions.jsonl').read_text().splitlines()) if d['record_id'] == 5)
revoked = args.state == 'revoked'
visible = security.catalog(token)
assert visible == ([] if revoked else [(1,12)]), visible
paths = ['/api/v1/chat/record/5/'+suffix for suffix in ['data','log','usage','predict_data']]
paths.append('/api/v1/chat/'+str(record['chat_id']))
if revoked:
    paths.append('/api/v1/chat/record/5/excel/export/'+str(record['chat_id']))
statuses = {}
for path in paths:
    status, body = real.request(path, token)
    statuses[path] = status
    assert status == (403 if revoked else 200), (path, status, body)
if revoked:
    result = real.ask('sqlbot4', '继续统计jt虚构订单源1的订单总金额', token=token, chat_id=record['chat_id'])
    assert any('no_query_permission' in e.get('content','') for e in result['events'] if e.get('type')=='error')
else:
    result = None
(real.A/('revocation-'+args.state+'.json')).write_text(json.dumps(dict(visible=visible, statuses=statuses,
    requestRecordId=result['record_id'] if result else None),indent=2))
if args.state == 'restored':
    private.unlink()
print('REVOCATION_'+args.state.upper()+'_OK')
