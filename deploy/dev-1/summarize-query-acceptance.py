"""Evaluate the last fixed 30-query batch and post-fix cloud security traces."""
import argparse
import json
import math
import re
import subprocess
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('--min-record', type=int, required=True, help='First record on the verified release')
args = parser.parse_args()
root = Path('/data/code/jt/deployment-audit/20260921-sqlbot-acceptance')
cases = [d for d in map(json.loads, (root/'real-questions.jsonl').read_text().splitlines())
         if d['record_id'] and d['record_id'] >= args.min_record]
by_id = {d['record_id']: d for d in cases}
ids = ','.join(map(str, by_id))
with Path(__file__).with_name('collect-query-audit.py').open() as script:
    process = subprocess.run(['docker','exec','-i','-e','SQLBOT_ACCEPT_RECORD_IDS='+ids,
        'dev-1-sqlbot-backend','python','-'], stdin=script, capture_output=True, text=True, check=True)
audit = json.loads(process.stdout)
restricted = {d['record_id'] for d in cases if d['user']=='sqlbot4'}
for item in audit:
    if item['id'] in restricted:
        assert all((c['datasourceId'],c['tableId'])==(1,12) for c in item['candidateIdentities']), item['id']
        assert 'public.customers' not in item.get('sqlContext',''), item['id']
    item.pop('sqlContext', None)  # Keep hash and table/field identities, not full prompts.
(root/'query-audit-final.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2))
lines = []
for path in Path('/data/service/dev-1/sqlbot/logs').glob('info.log*'):
    lines.extend(path.read_text().splitlines())
trace = [line for line in lines if 'Platform record_id=' in line
         and int(re.search(r'record_id=(\d+)',line).group(1)) in by_id]
(root/'execution-trace-final.log').write_text('\n'.join(trace)+'\n')
executed = {int(re.search(r'record_id=(\d+)',line).group(1)) for line in trace if 'execution_begin' in line}
rejected = [d['record_id'] for d in cases if any(e.get('type') in ('error','guidance') for e in d['events'])
            and not any(e.get('stage')=='query' and e.get('status') in ('running','completed') for e in d['events'])]
assert not set(rejected)&executed, set(rejected)&executed
expected = list(map(json.loads,(root/'expected-questions.jsonl').read_text().splitlines()))[-30:]
results = []
for e in expected:
    actual = by_id[e['record_id']]
    numbers = [float(v) for row in actual.get('result',{}).get('data',{}).get('data',[])
               for v in row.values() if isinstance(v,(int,float))]
    results.append(dict(record_id=e['record_id'],expected=e['expected'],actual=numbers,
        passed=sorted(numbers)==sorted(e['expected']),elapsed=actual['elapsedSeconds']))
(root/'fixed-questions-final.json').write_text(json.dumps(results,indent=2))
tested = {e['record_id'] for e in expected}
recall = [float(re.search(r'recall_ms=([\d.]+)',line).group(1))/1000 for line in trace
          if 'recall_ms=' in line and int(re.search(r'record_id=(\d+)',line).group(1)) in tested]
selection = [stage['seconds'] for item in audit if item['id'] in tested
             for stage in item['stages'] if stage['operation']=='6' and stage['seconds'] is not None]
def p95(values):
    return round(sorted(values)[math.ceil(.95*len(values))-1],3) if values else None
metrics = dict(sampleCount=len(tested),recallSamples=len(recall),recallP95=p95(recall),
    selectionP95=p95(selection),overallP95=p95([r['elapsed'] for r in results]),
    overallMax=max(r['elapsed'] for r in results),noExecutionRecords=rejected,restrictedContextPassed=True)
metrics['functionalPassed'] = len(results)==30 and all(r['passed'] for r in results)
metrics['performancePassed'] = len(recall)==30 and metrics['recallP95']<=2 and metrics['selectionP95']<=10 and metrics['overallP95']<=30
(root/'performance-and-trace.json').write_text(json.dumps(metrics,indent=2))
print(json.dumps(metrics))
assert metrics['functionalPassed'], [r for r in results if not r['passed']]
