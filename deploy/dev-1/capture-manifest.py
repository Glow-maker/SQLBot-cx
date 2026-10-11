"""Capture public release evidence only; never export environment values or tokens."""
import hashlib
import json
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

audit = Path('/data/code/jt/deployment-audit/20260921-sqlbot-integration')
def run(*args):
    return subprocess.check_output(args, text=True).strip()
def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()

repos = {'backend': '/data/code/jt/integration-20260921/bcback',
         'frontend': '/data/code/jt/integration-20260921/frontend', 'sqlbot': '/data/code/jt/SQLBot-cx'}
manifest = {'commits': {key: run('git', '-C', path, 'rev-parse', 'HEAD') for key, path in repos.items()},
            'artifacts': {}, 'javaTests': {}, 'images': {}}
for key, path in {'dataJar': '/data/service/dev-1/yudao-data/current/yudao-module-data-biz.jar',
                  'mainIndex': '/data/service/dev-1/academy8-operation-control-center-frontend/current/index.html'}.items():
    manifest['artifacts'][key] = {'path': path, 'sha256': digest(path)}
for name in ['dev-1-sqlbot-backend', 'dev-1-sqlbot-frontend']:
    details = json.loads(run('docker', 'inspect', name))[0]
    manifest['images'][name] = {'tag': details['Config']['Image'], 'id': details['Image'],
                               'status': details['State']['Status']}
for module in ['data', 'cost']:
    reports = Path(repos['backend']) / f'yudao-module-{module}/yudao-module-{module}-biz/target/surefire-reports'
    rows = []
    for report in reports.glob('TEST-*.xml'):
        root = ET.parse(report).getroot()
        if any(key in root.get('name', '') for key in ['Sqlbot', 'IndexSync', 'Cost']):
            rows.append({key: root.get(key) for key in ['name', 'tests', 'failures', 'errors', 'skipped']})
    manifest['javaTests'][module] = rows
audit.joinpath('release-manifest.json').write_text(json.dumps(manifest, indent=2))
print('RELEASE_MANIFEST_WRITTEN')
