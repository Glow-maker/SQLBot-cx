"""Real-model acceptance against the two fictional dev-1 sources only."""
import argparse
import json
import time
import urllib.request
import urllib.error
from pathlib import Path

A = Path('/data/code/jt/deployment-audit/20260921-sqlbot-acceptance')
OLD = A.parent / '20260921-sqlbot-integration'
BASE = 'http://127.0.0.1:20170'


def credentials():
    result = {}
    for p in (OLD/'accounts.private', A/'accounts.private'):
        result.update(dict(line.split('=', 1) for line in p.read_text().splitlines() if '=' in line))
    return result


def request(path, token=None, body=None, tenant=124):
    headers = {'tenant-id': str(tenant), 'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer '+token
    req = urllib.request.Request(BASE+path, headers=headers,
        data=None if body is None else json.dumps(body, ensure_ascii=False).encode())
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or '{}')


def login(user):
    status, body = request('/admin-api/system/auth/login', body={
        'username': user, 'password': credentials()[user+'.password']})
    assert status == 200 and body.get('code') == 0, (user, status, body.get('code'))
    return body['data']['accessToken']


def ask(user, question, token=None, chat_id=None):
    token = token or login(user)
    if chat_id is None:
        status, response = request('/api/v1/chat/start', token, {})
        assert status == 200, (status, response)
        chat_id = response.get('data', response)['id']
    started = time.monotonic()
    req = urllib.request.Request(BASE+'/api/v1/chat/question', headers={
        'Authorization': 'Bearer '+token, 'tenant-id': '124', 'Content-Type': 'application/json'},
        data=json.dumps({'chat_id': chat_id, 'question': question}, ensure_ascii=False).encode())
    events = []
    with urllib.request.urlopen(req, timeout=150) as r:
        for raw in r:
            if raw.startswith(b'data:'):
                events.append(json.loads(raw[5:]))
    result = dict(user=user, question=question, chat_id=chat_id,
                  elapsedSeconds=round(time.monotonic()-started, 3), events=events)
    ids = [e['id'] for e in events if e.get('type') == 'id']
    result['record_id'] = ids[0] if ids else None
    if ids:
        status, data = request('/api/v1/chat/record/'+str(ids[0])+'/data', token)
        result['dataStatus'], result['result'] = status, data
    with (A/'real-questions.jsonl').open('a') as output:
        output.write(json.dumps(result, ensure_ascii=False)+'\n')
    print(json.dumps({k: result.get(k) for k in ('user', 'question', 'record_id', 'elapsedSeconds', 'dataStatus')}, ensure_ascii=False), flush=True)
    return result


QUESTIONS = [
    ('统计jt虚构订单源1的订单总金额', [600]),
    ('统计jt虚构订单源1的订单总条数', [3]),
    ('查询jt虚构订单源1中金额大于150的订单数量', [2]),
    ('查询jt虚构订单源1的平均订单金额', [200]),
    ('查询jt虚构订单源1的最大订单金额', [300]),
    ('查询jt虚构订单源1的最小订单金额', [100]),
    ('查询jt虚构订单源1的客户数量', [2]),
    ('统计jt虚构订单源1每个客户姓名对应的订单总金额', [300, 300]),
    ('统计jt虚构订单源1每个客户姓名对应的订单数量', [1, 2]),
    ('查询jt虚构订单源1金额大于150的订单金额合计', [500]),
]


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--batch', action='store_true')
    args = parser.parse_args()
    A.mkdir(exist_ok=True)
    if args.batch:
        for question, expected in QUESTIONS:
            for repeat in range(3):
                result = ask('sqlbot1', question)
                # Preserve expected values separately; evaluate actual returned
                # fields/rows, never treat SSE finish alone as answer correctness.
                with (A/'expected-questions.jsonl').open('a') as output:
                    output.write(json.dumps(dict(record_id=result['record_id'], expected=expected))+'\n')
    else:
        for user, question in [('sqlbot1', QUESTIONS[0][0]), ('sqlbot4', QUESTIONS[0][0]),
                               ('sqlbot2', QUESTIONS[0][0]), ('sqlbot1', '你好')]:
            ask(user, question)
