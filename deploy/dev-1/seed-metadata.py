"""Seed only empty dataease_dev1 with fabricated data and explicit new user IDs."""
import json,subprocess,sys
from pathlib import Path
assert len(sys.argv)==3, 'reader and index-manager user IDs required'
reader,manager=map(int,sys.argv[1:])
assert reader>0 and manager>0 and reader!=manager
root=Path('/data/code/jt/deployment-audit/20260921-sqlbot-integration')
password=json.loads((root/'database-secrets.json').read_text())['demo_password']
def mysql(sql):
    return subprocess.check_output(['docker','exec','-i','mysql-local','sh','-c','MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysql -uroot -N'],input=sql.encode()).decode()
assert mysql('SELECT COUNT(*) FROM dataease_dev1.dmgt_data_source_config;').strip()=='0'
assert mysql('SELECT COUNT(*) FROM dataease_dev1.dmgt_namespace;').strip()=='0'
def val(v):return str(v) if isinstance(v,int) else "CONVERT(0x"+str(v).encode().hex()+" USING utf8mb4)"
sql=['USE dataease_dev1;','START TRANSACTION;']
def add(table,**row):sql.append('INSERT INTO '+table+' ('+','.join('`'+k+'`' for k in row)+') VALUES ('+','.join(val(v) for v in row.values())+');')
add('dmgt_namespace',id=1,no='JT-SQLBOT-DEMO',name='jt智能问数测试',organization=1,status='ENABLED',tenant_id=124,is_default=1)
for level in range(1,4):
    add('dmgt_category',id=level,namespace_id=1,parent_id=level-1,no='JT-DEMO-L'+str(level),name=['测试主题','订单管理','模拟业务'][level-1],level=level,status='ENABLED',approval_status='APPROVED',tenant_id=124)
for ds,db in [(1,'sqlbot_demo_dev1'),(2,'sqlbot_demo_alt_dev1')]:
    add('dmgt_data_source_config',id=ds,db_type='POSTGRESQL',name='jt虚构订单源'+str(ds),driver_type='org.postgresql.Driver',url='jdbc:postgresql://8.130.191.105:13252/'+db,username='sqlbot_demo_reader_dev1',password=password,status='ENABLED',test_result='SUCCESS',host='8.130.191.105',port='13252',db_name=db,db_schema='public',tenant_id=124)
    for j,(table,comment,fields) in enumerate([
        ('customers','虚构客户表',[('id','bigint','客户编号'),('name','varchar','客户名称'),('region','varchar','区域')]),
        ('orders','虚构订单表',[('id','bigint','订单编号'),('customer_id','bigint','客户编号'),('order_date','date','订单日期'),('amount','numeric','订单金额')])
    ],1):
        tid=ds*10+j; cid=100+tid
        add('dmgt_table',id=tid,table_name=table,schema_name='public',data_source_id=ds,table_comment=comment,table_type='TABLE',tenant_id=124)
        add('dmgt_category',id=cid,namespace_id=1,parent_id=3,no='JT-DEMO-'+str(tid),name=comment+'源'+str(ds),level=4,status='ENABLED',mount_status='MOUNTED',approval_status='APPROVED',table_id=tid,tenant_id=124)
        add('dmgt_category_ext',id=cid,category_id=cid,field_size=len(fields),data_size=2 if j==1 else 3,stat_date='2026-09-21')
        for n,(name,kind,desc) in enumerate(fields,1):
            add('dmgt_table_field',id=tid*100+n,table_id=tid,name=name,type=kind,alias=desc,remark=desc,sort=n,status='ENABLED',content_visible='VISIBLE',tenant_id=124)
        add('dmgt_role',id=tid,tenant_id=124,scope='category',role_type='READER',resource_id=cid)
        for uid in [reader,manager]:add('dmgt_user_role',id=tid*1000+uid,user_type='USER',user_id=uid,role_id=tid,tenant_id=124)
sql.append('COMMIT;')
mysql('\n'.join(sql))
print('JT_METADATA_SEEDED sources=2 tables=4 fields=14 namespace=1')
