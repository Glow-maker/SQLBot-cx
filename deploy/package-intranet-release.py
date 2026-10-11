"""Assemble the freshly built June-targeted release. Does not deploy or touch databases.

Usage: python deploy/package-intranet-release.py --workspace H:/light/project/sqlbot-release-20260909
Build commands and reviewed inputs are recorded in the manifest; existing legacy packaging
scripts do not include all the frontend/scheduler/permission changes and must not be used.
"""
import argparse
import hashlib
import json
import re
import shutil
import subprocess
import tarfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
VERSION = '20260909'
TAGS = ['sqlbot-cx-backend-host:intranet-20260909', 'sqlbot-cx-frontend:intranet-20260909']

def sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(4*1024*1024), b''):
            digest.update(block)
    return digest.hexdigest()

def run(*cmd, cwd=None):
    return subprocess.check_output(list(cmd), cwd=cwd)

def write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding='utf-8', newline='\n')

def zip_tree(folder, output):
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED,compresslevel=6,allowZip64=True) as z:
        for p in sorted(folder.rglob('*')):
            if p.is_file(): z.write(p,p.relative_to(folder).as_posix())

def main():
    parser=argparse.ArgumentParser(__doc__)
    parser.add_argument('--workspace',type=Path,required=True)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args(); work=args.workspace.resolve()
    out=(args.output or ROOT/'deploy/package'/f'sqlbot-intranet-upgrade-{VERSION}-linux-amd64').resolve()
    if out.exists(): raise RuntimeError('Output already exists; select a new path to preserve prior artifacts')
    required={'java-build.log':'BUILD SUCCESS','platform-build.log':'Build successful',
              'sqlbot-frontend-build.log':'built in','sqlbot-ui-tests.log':'# pass 23',
              'pg-index-tests.log':'Ran 32 tests','offline-model.log':'OFFLINE_MODEL_OK',
              'pg-upgrade.log':'a6e066b1c001','pg-rollback.log':'8ff90df7871d'}
    for name,marker in required.items():
        if marker not in (work/name).read_text('utf-8',errors='replace'): raise RuntimeError('Missing successful evidence: '+name)
    audit=json.loads((work/'artifact-scan.json').read_text('utf-8'))
    if audit['hits'] or audit['environmentProfilesInJar']: raise RuntimeError('Artifact scan needs review')
    images=json.loads(run('docker','image','inspect',*TAGS))
    if any(i['Architecture']!='amd64' or i['Os']!='linux' for i in images): raise RuntimeError('Wrong image architecture')
    tests=[]
    testroot=work/'bcback/yudao-module-data/yudao-module-data-biz/target/surefire-reports'
    for p in sorted(testroot.glob('TEST-*.xml')):
        tree=ET.parse(p).getroot(); row={k:tree.attrib[k] for k in ('name','tests','failures','errors','skipped')}
        if int(row['failures']) or int(row['errors']) or int(row['skipped']): raise RuntimeError('Java tests not passed: '+str(row))
        tests.append(row)
    if sum(int(t['tests']) for t in tests) < 37: raise RuntimeError('Missing June compatibility tests')
    out.mkdir(parents=True)
    for d in ('images','platform','config','database','scripts','docs/evidence','source'): (out/d).mkdir(parents=True,exist_ok=True)
    templates=ROOT/'deploy/intranet-upgrade'
    for p in templates.rglob('*'):
        if p.is_file(): shutil.copy2(p,out/p.relative_to(templates))
    jar=work/'bcback/yudao-module-data/yudao-module-data-biz/target/yudao-module-data-biz.jar'
    shutil.copy2(jar,out/'platform/yudao-module-data-biz.jar')
    dist=work/'platform-frontend/dist-prod'
    assert (dist/'index.html').is_file()
    zip_tree(dist,out/'platform/platform-frontend.zip')
    ddl=(work/'bcback/yudao-module-data/sql/DDL.sql').read_text('utf-8')
    assert ddl.upper().count('CREATE TABLE')==1 and 'ALTER TABLE' not in ddl.upper()
    assert 'dmgt_index_sync_control' in ddl and 'dmgt_table_relation' not in ddl
    write(out/'database/mysql-index-sync-control.sql',ddl)
    write(out/'database/PostgreSQL升级说明.txt',
          '实测迁移起点：8ff90df7871d；目标：a6e066b1c001（066）。\n'
          'SQLBot主服务启动自动执行alembic upgrade head。仅新增platform_table_embedding、platform_table_embedding_chunk、platform_embedding_sync_job和索引；需要pgvector。\n'
          '本包不含PG数据目录、数据库镜像或整库初始化脚本。其他revision先停止并审查迁移链，不执行stamp掩盖差异。\n'
          '回滚旧镜像前必须阅读docs/回滚教程.md：旧Alembic不能识别新revision。保留新表时再次升级也需按教程核实版本记录。\n')
    # Whitelisted source files only. Exclude local env, config profiles, logs, build outputs,
    # DB probes, archives and docs that may contain credentials. Full git history is not exported.
    suffixes={'.py','.java','.xml','.ts','.tsx','.js','.cjs','.mjs','.vue','.scss','.css','.less','.json','.toml','.lock','.ini','.html','.svg','.png','.jpg','.jpeg','.gif','.woff','.woff2','.ttf','.sh','.sql'}
    blocked={'node_modules','target','dist','dist-prod','.git','.idea','.venv','__pycache__','logs','outputs','.codegraph'}
    sources={}; inventory=[]
    sourcezip=out/'source/定向补丁与源码快照.zip'
    with zipfile.ZipFile(sourcezip,'w',zipfile.ZIP_DEFLATED,compresslevel=6,allowZip64=True) as z:
        for name,repo,baseline in (
            ('bcback',work/'bcback','4208811fe9df91eb6c31efae82fbf420ceec11a3'),
            ('platform-frontend',work/'platform-frontend','d3b41feeb80eed791d480e921c1378fea959996d'),
            ('sqlbot',work/'sqlbot',run('git','rev-parse','HEAD',cwd=ROOT).decode().strip())):
            if name!='sqlbot':
                candidates=run('git','ls-files','-co','--exclude-standard','-z',cwd=repo).decode('utf-8').split('\0')
            else:
                candidates=[]
                # os.walk pruning prevents traversing node_modules junctions/model weights.
                import os
                for parent,dirs,files in os.walk(repo):
                    dirs[:]=[d for d in dirs if d not in blocked and (Path(parent)/d).relative_to(repo).as_posix() not in {'deploy/models','backend/data'}]
                    candidates.extend(str((Path(parent)/f).relative_to(repo)) for f in files)
            entries=[]
            for relative in sorted(set(candidates)):
                if not relative: continue
                rel=Path(relative); p=repo/rel
                if not p.is_file() or any(x in blocked for x in rel.parts): continue
                if p.name.startswith('.env') or p.name in {'start.ps1'}: continue
                if name=='bcback' and '/src/test/' in '/'+rel.as_posix() and not p.name.startswith(('Sqlbot','IndexSync')): continue
                if any(x.startswith('application-') or x.startswith('bootstrap') for x in rel.parts): continue
                common_config=name=='bcback' and rel.as_posix()=='yudao-module-data/yudao-module-data-biz/src/main/resources/application.yml'
                if p.suffix not in suffixes and not p.name.startswith('Dockerfile') and p.name!='.dockerignore' and not common_config: continue
                if p.suffix=='.sql' and relative.replace('\\','/')!='yudao-module-data/sql/DDL.sql': continue
                if p.name.startswith(('mock_platform','verify_')): continue
                # Config-free snapshot: runtime configuration is supplied on site.
                data=p.read_bytes(); arc=f'{name}/{rel.as_posix()}'
                z.writestr(arc,data)
                item={'path':arc,'sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data)}
                entries.append(item);inventory.append(item)
            digest=hashlib.sha256(json.dumps(entries,sort_keys=True).encode()).hexdigest()
            if name!='sqlbot':
                patch=run('git','diff','--binary',baseline,'--','.',':(exclude).env*',':(exclude)**/application-*.yml',':(exclude)**/application-*.yaml',':(exclude)**/bootstrap*',cwd=repo)
            else:
                # Original dirty patch complements the complete effective source snapshot.
                patch=run('git','diff','--binary','HEAD','--','backend','frontend',':(exclude)**/.env*',':(exclude)**/logs/**',':(exclude)**/start.ps1',cwd=ROOT)
            z.writestr(name+'/tracked-changes.patch',patch)
            sources[name]={'baseline':baseline,'effectiveSnapshotSha256':digest,'trackedPatchSha256':hashlib.sha256(patch).hexdigest(),'fileCount':len(entries)}
        for extra in ('Dockerfile.backend','Dockerfile.frontend','nginx-sqlbot.conf','.dockerignore'):
            z.write(work/'sqlbot'/extra,'build/'+extra)
        z.write(work/'platform-frontend/.env.prod','build/platform.env.prod')
        z.write(Path(__file__),'build/package-intranet-release.py')
        z.writestr('source-inventory.json',json.dumps(inventory,ensure_ascii=False,indent=2))
        z.writestr('README.txt','Effective source snapshot; no local profiles, credentials, model weights or git history.\nTracked patches exclude environment profiles. New files and release adaptations are authoritative in the snapshot/inventory. Build runtime is pinned in the manifest and already included in the final images; rebuilding source needs the original runtime build inputs.\n')
    with zipfile.ZipFile(sourcezip) as archive:
        key_pattern=rb'eyJ[A-Za-z0-9_-]{18,}\.[A-Za-z0-9_-]{18,}\.[A-Za-z0-9_-]{16,}|\bsk-[A-Za-z0-9]{24,}\b'
        for name in archive.namelist():
            if name.endswith(('.java','.py','.vue','.json','.patch','.ts','.xml')) and re.search(key_pattern,archive.read(name)):
                raise RuntimeError('Source token pattern needs review: '+name)
    for name in list(required)+['artifact-scan.json','sse-transport.log','sqlbot-backend-build.log','sqlbot-frontend-image.log','runtime-smoke.json','gateway-transport.json']:
        p=work/name
        if not p.is_file(): raise RuntimeError('Missing evidence: '+name)
        shutil.copy2(p,out/'docs/evidence'/name)
    for p in testroot.glob('*.txt'): shutil.copy2(p,out/'docs/evidence'/p.name)
    report=(templates/'docs/版本变更与验证报告.md').read_text('utf-8')
    write(out/'docs/版本变更与验证报告.md',report)
    print('Saving both fresh images...',flush=True)
    subprocess.run(['docker','save','-o',str(out/'images/sqlbot-images.tar'),*TAGS],check=True)
    archive_configs={}
    with tarfile.open(out/'images/sqlbot-images.tar') as archive:
        for entry in json.load(archive.extractfile('manifest.json')):
            config=archive.extractfile(entry['Config']).read()
            for tag in entry.get('RepoTags',[]): archive_configs[tag]='sha256:'+hashlib.sha256(config).hexdigest()
    artifacts=[{'path':p.relative_to(out).as_posix(),'bytes':p.stat().st_size,'sha256':sha(p)} for p in sorted(out.rglob('*')) if p.is_file()]
    manifest={'version':VERSION,'createdAt':datetime.now(timezone.utc).isoformat(),'releaseStatus':'pending_integration_acceptance',
              'target':'linux/amd64','sources':sources,'model':{'name':'shibing624_text2vec-base-chinese','dimension':768,'maxTokens':128,'fingerprint':'ef9f49c2c260b7269c9090e0fa1e00f869bc8cced26380c2d749d882fb6454f1'},
              'images':[{'tag':tag,'id':i['Id'],'configDigest':archive_configs[tag],'os':i['Os'],'architecture':i['Architecture'],'created':i['Created'],'bytes':i['Size']} for tag,i in zip(TAGS,images)],
              'runtimeBaseId':'sha256:e4a0071976d13f00d58db723a46b12e5627584d629dd9929066b111492a9a2f6',
              'build':{'SQLBotUI':{'VITE_PUBLIC_BASE_URL':'/sqlbot-app/','VITE_API_BASE_URL':'/api/v1','VITE_PLATFORM_LOGICAL_DATASOURCE_ENABLED':'true','VITE_SHOW_REASONING':'false'},
                       'platformUI':'npm ci; npm run build:prod with isolated build/platform.env.prod',
                       'java':"mvn -pl yudao-module-data/yudao-module-data-biz -am package -Dtest=Sqlbot*Test,IndexSync*Test -Dsurefire.failIfNoSpecifiedTests=false -Dspotbugs.skip=true; SQLBOT_JUNE_MYSQL_TEST=1",
                       'images':'docker build --network=none; runtime dependencies reused, application/frontend freshly built'},
              'verification':{'java':tests,'pythonIndexAndProgress':{'tests':32,'passed':32,'encoder':'FakeEncoder for DB tests'},'frontend':{'tests':23,'passed':23},
                              'mysql':'8.0.39 isolated June DO-derived fixture, not production dump','postgres':'17 pgvector isolated migration/incremental/rollback',
                              'offlineModel':'real model network=none passed','nginx':'real nginx transport with fixture events, not real user question',
                              'intranet':'NOT RUN','authenticatedEndToEnd':'NOT VERIFIED; existing local platform 401 remains a release gate'},
              'artifacts':artifacts}
    write(out/'release-manifest.json',json.dumps(manifest,ensure_ascii=False,indent=2))
    sums=''.join(f'{sha(p)}  {p.relative_to(out).as_posix()}\n' for p in sorted(out.rglob('*')) if p.is_file())
    write(out/'SHA256SUMS',sums)
    archive=out.with_suffix('.zip')
    print('Compressing offline ZIP...',flush=True)
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED,compresslevel=4,allowZip64=True) as z:
        for p in sorted(out.rglob('*')):
            if p.is_file():z.write(p,out.name+'/'+p.relative_to(out).as_posix())
    write(archive.with_suffix('.zip.sha256'),f'{sha(archive)}  {archive.name}\n')
    print(json.dumps({'directory':str(out),'zip':str(archive),'bytes':archive.stat().st_size,'sha256':sha(archive)},ensure_ascii=False),flush=True)

if __name__=='__main__': main()
