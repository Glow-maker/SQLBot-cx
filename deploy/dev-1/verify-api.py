"""Read-only authentication, metadata and permission smoke tests; never logs tokens."""
import json,urllib.request,urllib.error
from pathlib import Path
A=Path('/data/code/jt/deployment-audit/20260921-sqlbot-integration')
credentials=dict(l.split('=',1) for l in (A/'accounts.private').read_text().splitlines() if '=' in l)
def request(base,path,token=None,body=None,tenant=124,method=None):
    headers={'tenant-id':str(tenant),'Content-Type':'application/json'}
    if token:headers['Authorization']='Bearer '+token
    req=urllib.request.Request(base+path,data=None if body is None else json.dumps(body).encode(),headers=headers,method=method)
    try:
        with urllib.request.urlopen(req,timeout=30) as r:return r.status,json.load(r)
    except urllib.error.HTTPError as e:
        try:return e.code,json.load(e)
        except Exception:return e.code,{}
def login(user):
    _,r=request('http://172.17.0.1:20180','/admin-api/system/auth/login',body={'username':user,'password':credentials[user+'.password']})
    assert r.get('code')==0,(user,r.get('code'),r.get('msg'))
    return r['data']['accessToken']
if __name__=='__main__':
    result=[]
    for user in ['sqlbot1','sqlbot2','sqlbot3']:
        token=login(user)
        status,r=request('http://127.0.0.1:20184','/openapi/sqlbot/datasources/query',token)
        assert r.get('code')==0,(user,'metadata',status,r.get('code'),r.get('msg'))
        status,p=request('http://127.0.0.1:20184','/openapi/sqlbot/table-permissions/check',token,{'datasourceId':1,'tables':[{'tableId':11,'name':'customers','schemaName':'public'}]})
        assert p.get('code')==0,(user,'permissions',p.get('code'),p.get('msg'))
        assert p['data']['allowed']==(user!='sqlbot2'),(user,'unexpected permission')
        status,index=request('http://172.17.0.1:20180','/admin-api/dmgt/data-source/index-sync',token)
        if user=='sqlbot3':assert index.get('code')==0,(status,index.get('code'),index.get('msg'))
        else:assert status==403 or index.get('code')==403,(user,'index not denied')
        result.append({'user':user,'login':True,'metadata':True,'queryAllowed':p['data']['allowed'],'indexConfigCode':index.get('code',status)})
    (A/'api-smoke.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(result))
