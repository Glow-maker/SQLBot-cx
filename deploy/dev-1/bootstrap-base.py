"""Run on base: guarded creation of jt-only databases. Secrets never printed."""
import json, os, secrets, subprocess
from pathlib import Path

os.umask(0o077)
root=Path('/data/code/jt/deployment-audit/20260921-sqlbot-integration')
root.mkdir(parents=True,exist_ok=True)
secret_file=root/'database-secrets.json'
if secret_file.exists():
    raise SystemExit('Existing bootstrap credentials: inspect before resuming')
def pg(sql,db='postgres'):
    return subprocess.check_output(['docker','exec','-i','-u','postgres','pg-local','psql','-X','-U','root','-d',db,'-v','ON_ERROR_STOP=1','-At'],input=sql.encode()).decode()
def mysql(sql):
    return subprocess.check_output(['docker','exec','-i','mysql-local','sh','-c','MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysql -uroot -N'],input=sql.encode()).decode()
names=['sqlbot_dev1','sqlbot_demo_dev1','sqlbot_demo_alt_dev1']
assert pg("SELECT count(*) FROM pg_database WHERE datname IN ('sqlbot_dev1','sqlbot_demo_dev1','sqlbot_demo_alt_dev1');").strip()=='0'
assert pg("SELECT count(*) FROM pg_roles WHERE rolname IN ('sqlbot_dev1','sqlbot_demo_reader_dev1');").strip()=='0'
assert mysql("SELECT COUNT(*) FROM information_schema.SCHEMATA WHERE SCHEMA_NAME='dataease_dev1';").strip()=='0'
assert mysql("SELECT COUNT(*) FROM mysql.user WHERE User='dataease_dev1';").strip()=='0'
dump=Path(__file__).with_name('sql').joinpath('schema-mysql.sql').read_bytes()
assert dump.count(b'CREATE TABLE ') == 23 and b'dmgt_index_sync_control' in dump
credentials={k:secrets.token_hex(24) for k in ['pg_password','mysql_password','demo_password','app_secret']}
secret_file.write_text(json.dumps(credentials))
pg(f"CREATE ROLE sqlbot_dev1 LOGIN PASSWORD '{credentials['pg_password']}'; CREATE ROLE sqlbot_demo_reader_dev1 LOGIN PASSWORD '{credentials['demo_password']}';")
for name in names:
    pg(f'CREATE DATABASE {name};')
    pg(f'REVOKE ALL ON DATABASE {name} FROM PUBLIC;')
pg('ALTER DATABASE sqlbot_dev1 OWNER TO sqlbot_dev1;')
pg('CREATE EXTENSION vector; GRANT ALL ON SCHEMA public TO sqlbot_dev1;','sqlbot_dev1')
for name in names[1:]:
    pg(f'GRANT CONNECT ON DATABASE {name} TO sqlbot_demo_reader_dev1;')
    pg('''CREATE TABLE public.customers (id bigint PRIMARY KEY, name varchar(80) NOT NULL, region varchar(40) NOT NULL);
COMMENT ON TABLE public.customers IS 'jt虚构客户测试表';
CREATE TABLE public.orders (id bigint PRIMARY KEY, customer_id bigint REFERENCES public.customers(id), order_date date NOT NULL, amount numeric(12,2) NOT NULL);
COMMENT ON TABLE public.orders IS 'jt虚构订单测试表';
INSERT INTO public.customers VALUES (1,'模拟客户甲','华东'),(2,'模拟客户乙','华北');
INSERT INTO public.orders VALUES (1,1,'2026-09-01',100),(2,1,'2026-09-02',200),(3,2,'2026-09-03',300);
GRANT USAGE ON SCHEMA public TO sqlbot_demo_reader_dev1;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO sqlbot_demo_reader_dev1;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;''',name)
(root/'mysql-create-only.sql').write_bytes(dump)
mysql(f"CREATE DATABASE dataease_dev1 CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci; CREATE USER 'dataease_dev1'@'%' IDENTIFIED BY '{credentials['mysql_password']}'; GRANT ALL ON dataease_dev1.* TO 'dataease_dev1'@'%';")
mysql('USE dataease_dev1;\n'+dump.decode())
print('JT_DATABASES_CREATED pg=3 mysql=1 metadata_tables=23')
