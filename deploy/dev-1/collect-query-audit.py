"""Run inside SQLBot; export only owned acceptance records, never connection config."""
import hashlib
import json
import os
from sqlalchemy import text, bindparam
from common.core.db import engine

ids = [int(i) for i in os.environ['SQLBOT_ACCEPT_RECORD_IDS'].split(',')]
result = []
with engine.connect() as connection:
    records = connection.execute(text('SELECT id, create_by, datasource, sql, error FROM chat_record WHERE id IN :ids')
                                 .bindparams(bindparam('ids', expanding=True)), {'ids': ids}).mappings()
    for record in records:
        output = dict(record)
        output['stages'], output['candidateIdentities'] = [], []
        logs = connection.execute(text('SELECT operate, messages, start_time, finish_time, token_usage FROM chat_log WHERE pid=:id ORDER BY id'), {'id':record['id']}).mappings()
        for log in logs:
            output['stages'].append(dict(operation=log['operate'], seconds=(log['finish_time']-log['start_time']).total_seconds()
                if log['finish_time'] and log['start_time'] else None, tokens=log['token_usage']))
            for message in log['messages'] or []:
                if not isinstance(message, dict):
                    continue
                content = message.get('content','')
                if log['operate'] == '6' and message.get('type') == 'human':
                    cards = json.loads(content).get('tables',[])
                    output['candidateIdentities'] = [{k:c.get(k) for k in ('datasourceId','tableId','schemaName','name','fields','relations')} for c in cards]
                if log['operate'] == '0' and message.get('type') == 'system':
                    context = content.split('以下是正式的信息：')[-1]
                    output['sqlContextHash'] = hashlib.sha256(context.encode()).hexdigest()
                    output['sqlContext'] = context
        result.append(output)
print(json.dumps(result,ensure_ascii=False,default=str))
