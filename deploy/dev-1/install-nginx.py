"""Restore only dev-1 SQLBot routes in the shared, mounted Nginx config."""
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import re
import shutil
import subprocess


def add_routes(original, locations):
    # Fail closed if the shared config no longer has this top-level layout.
    blocks = list(re.finditer(r'^server\s*\{\s*\n.*?^\}', original, re.M | re.S))
    owned = [b for b in blocks if re.search(r'^\s*listen\s+20170\s*;', b[0], re.M)]
    if len(owned) != 1:
        raise RuntimeError('Expected exactly one dev-1 server listening on 20170')
    block = owned[0]
    snippet = '\n'.join('    ' + line for line in locations.strip().splitlines())
    if snippet in block[0]:
        return original
    if any(path in block[0] for path in ('/sqlbot-app', '/api/v1/', '/images/')):
        raise RuntimeError('Partial or different SQLBot routes exist; inspect before changing')
    replacement = block[0][:-1] + '\n' + snippet + '\n}'
    return original[:block.start()] + replacement + original[block.end():]


def main():
    target = Path('/data/deploy/nginx/conf.d/default.conf')
    mounts = json.loads(subprocess.check_output(['docker', 'inspect', 'nginx']))[0]['Mounts']
    if not any(m['Source'] == str(target.parent) and m['Destination'] == '/etc/nginx/conf.d'
               for m in mounts):
        raise RuntimeError('Nginx mount differs from expected shared config directory')
    before = target.read_bytes()
    locations = Path(__file__).with_name('nginx.locations.conf').read_text()
    after = add_routes(before.decode('utf-8'), locations).encode('utf-8')
    if after == before:
        subprocess.run(['docker', 'exec', 'nginx', 'nginx', '-t'], check=True)
        print('DEV1_NGINX_ROUTES_UNCHANGED')
        return
    audit = Path('/data/code/jt/deployment-audit') / datetime.now(timezone.utc).strftime('nginx-dev1-%Y%m%dT%H%M%S%fZ')
    audit.mkdir(parents=True, mode=0o700)
    shutil.copy2(target, audit / 'default.before.conf')
    (audit / 'default.after.conf').write_bytes(after)
    if target.read_bytes() != before:
        raise RuntimeError('Config changed concurrently; retry after inspection')
    target.write_bytes(after)
    try:
        subprocess.run(['docker', 'exec', 'nginx', 'nginx', '-t'], check=True)
        subprocess.run(['docker', 'exec', 'nginx', 'nginx', '-s', 'reload'], check=True)
    except Exception:
        if target.read_bytes() == after:
            target.write_bytes(before)
            subprocess.run(['docker', 'exec', 'nginx', 'nginx', '-t'], check=True)
            subprocess.run(['docker', 'exec', 'nginx', 'nginx', '-s', 'reload'], check=True)
        raise
    print(json.dumps({'result': 'DEV1_NGINX_ROUTES_RELOADED', 'backup': str(audit),
                      'before_sha256': hashlib.sha256(before).hexdigest(),
                      'after_sha256': hashlib.sha256(after).hexdigest()}))


if __name__ == '__main__':
    main()
