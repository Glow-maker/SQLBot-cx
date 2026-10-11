"""Controlled SQLBot API acceptance against the two isolated demo sources only."""
import importlib.util
import json
import time
from pathlib import Path

spec = importlib.util.spec_from_file_location('api', Path(__file__).with_name('verify-api.py'))
api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api)
base = 'http://172.17.0.1:20186'
path = '/api/v1/platform/table-embeddings/sync'
tokens = {u: api.login(u) for u in ['sqlbot1', 'sqlbot2', 'sqlbot3']}
result = {'authentication': [], 'jobs': []}
for token, tenant, expected in [(None, 124, 401), ('expired', 124, 401), (tokens['sqlbot3'], 999, 403)]:
    status, _ = api.request(base, path + '?datasourceId=1', token, tenant=tenant)
    assert status == expected, (status, expected)
    result['authentication'].append(status)
for user in ['sqlbot1', 'sqlbot2']:
    status, response = api.request(base, path, tokens[user], {'datasourceId': 1})
    assert status == 403, (user, status)
    result['authentication'].append(status)

def unwrap(body):
    return body.get('data', body) if isinstance(body, dict) else body

for round_number in [1, 2]:
    for source in [1, 2]:
        status, body = api.request(base, path, tokens['sqlbot3'], {'datasourceId': source})
        assert status == 202, (status, body)
        job = unwrap(body)
        job_id = job['jobId']
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            status, body = api.request(base, path + '/' + job_id, tokens['sqlbot3'])
            job = unwrap(body)
            if job['status'] not in ['pending', 'running']:
                break
            time.sleep(2)
        assert job['status'] == 'succeeded', job
        assert job['processed'] == 2 and job['failed'] == 0, job
        if round_number == 2:
            assert job['skipped'] == 2, job
        result['jobs'].append(job)
        print('INDEX_JOB_OK', round_number, source, job['added'], job['updated'], job['skipped'], flush=True)
api.A.joinpath('sqlbot-runtime-acceptance.json').write_text(json.dumps(result, indent=2))
print('SQLBOT_RUNTIME_ACCEPTANCE_OK')
