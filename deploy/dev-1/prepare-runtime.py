"""Prepare protected dev-1 environment files from the recorded bootstrap secrets."""
import json,os,re,shlex
from pathlib import Path
os.umask(0o077)
a=Path('/data/code/jt/deployment-audit/20260921-sqlbot-integration')
secret=json.loads((a/'database-secrets.json').read_text())
connections=json.loads((a/'original-data-connections.json').read_text())
dm=next(c for c in connections if c[0].startswith('jdbc:dm:'))
old=Path('/data/code/jt/bcback/yudao-module-data/yudao-module-data-biz/src/main/resources/application-dev-1.yml').read_text()
nacos=re.search(r'(?m)^      password: (.+)$',old).group(1).strip()
data=dict(DATA_DM_URL=dm[0],DATA_DM_USERNAME=dm[1],DATA_DM_PASSWORD=dm[2],
    DATA_MYSQL_URL='jdbc:mysql://8.130.191.105:13251/dataease_dev1?allowPublicKeyRetrieval=true&useSSL=false',
    DATA_MYSQL_USERNAME='dataease_dev1',DATA_MYSQL_PASSWORD=secret['mysql_password'],NACOS_PASSWORD=nacos,
    DATA_SQLBOT_INDEX_API_BASE_URL='http://172.17.0.1:20186/api/v1',DATA_CATEGORY_REFRESH_CRON='-')
(a/'data-service-next.env').write_text((a/'data-service.env').read_text()+'\n'+''.join(k+'='+shlex.quote(v)+'\n' for k,v in data.items()))
dest=Path('/data/service/dev-1/sqlbot')
dest.mkdir(exist_ok=True)
env=dict(POSTGRES_SERVER='8.130.191.105',POSTGRES_PORT='13252',POSTGRES_DB='sqlbot_dev1',
    POSTGRES_USER='sqlbot_dev1',POSTGRES_PASSWORD=secret['pg_password'],SECRET_KEY=secret['app_secret'],
    SQLBOT_BIND_HOST='172.17.0.1',BACKEND_PORT='20186',MCP_PORT='20187',G2_PORT='20189',
    MCP_IMAGE_HOST='http://127.0.0.1:20189',SERVER_IMAGE_HOST='http://8.130.92.192:20170/images/',
    FRONTEND_HOST='http://8.130.92.192:20170',BACKEND_CORS_ORIGINS='http://8.130.92.192:20170',
    PLATFORM_AUTH_ENABLED='true',PLATFORM_AUTH_STRICT_MODE='true',PLATFORM_AUTH_ALLOW_LOCAL_ADMIN_LOGIN='false',
    PLATFORM_AUTH_BASE_URL='http://172.17.0.1:20180',PLATFORM_AUTH_REDIS_URL='redis://127.0.0.1:6379/1',
    PLATFORM_AUTH_REDIS_KEY_PREFIX='oauth2_access_token:%s',PLATFORM_AUTH_REQUIRE_USER_TYPE='2',
    PLATFORM_AUTH_REQUIRE_TENANT_ID_HEADER='true',PLATFORM_AUTH_FIXED_TENANT_ID='124',
    PLATFORM_DATASOURCE_ENABLED='true',PLATFORM_DATASOURCE_BASE_URL='http://127.0.0.1:20184',
    PLATFORM_LOGICAL_DATASOURCE_ENABLED='true',PLATFORM_LOGICAL_ROUTE_RECALL_ENABLED='true',
    PLATFORM_LOGICAL_ROUTE_EMBEDDING_ENABLED='true',HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',
    LOCAL_MODEL_PATH='/opt/sqlbot/models',CACHE_TYPE='memory',OMP_NUM_THREADS='2',TOKENIZERS_PARALLELISM='false')
out=dest/'backend.env'
if out.exists():raise SystemExit('Runtime env exists; inspect before replacing')
out.write_text(''.join(k+'='+v+'\n' for k,v in env.items()))
for name in ['excel','file','images','logs']: (dest/name).mkdir(exist_ok=True)
print('RUNTIME_ENV_PREPARED secrets_not_printed')
