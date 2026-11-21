"""
隔离单端到端核对（gb-4109）
SQLite 运行，不依赖 Docker/PostgreSQL/Redis；验证：
 1. 登记隔离单并按检测项+检测时间标记受影响结果
 2. 多单同时命中，撤销一张其余保留
 3. 生效/撤销一次性切换、版本号单调、单次核对同版
 4. 切换失败整体回滚，旧版保留
 5. 重试同一单幂等，不重复生效/升版
 6. 切换期间迁移落库与旧备份恢复并发，切换完成覆盖期间写入结果
 7. 原结果与备份摘要(checksum/逐行 hash)不受隔离影响
 8. 角色权限
运行：python3 -m pytest test-artifacts/gb-4109/test_quarantine.py -q
"""
import os,json,threading,time
from datetime import datetime,timezone,timedelta

os.environ.setdefault('JWT_SECRET','test-secret')
os.environ.setdefault('DATABASE_URL','sqlite:////tmp/gb4109_test.db')
os.environ.setdefault('REDIS_URL','redis://localhost:6379/0')
if os.path.exists('/tmp/gb4109_test.db'):os.remove('/tmp/gb4109_test.db')

import pytest
from sqlalchemy.orm import Query
from fastapi.testclient import TestClient

# SQLite 不支持 SELECT ... FOR UPDATE，降级为普通查询（本测试用线程/顺序控制验证逻辑）
Query.with_for_update=lambda self,*a,**k:self

from src.config.database import Base,engine,Session
from src.models.identity import User
from src.models.batch import Batch
from src.models.mapping import Mapping
from src.models.run import Run,TargetRow
from src.models.quarantine import Quarantine,IsolationVersion
from src.utils.security import hash_password,token
from src.services.migration import migrate,make_backup,key_digest,digest
from src.main import app

RULES=[{'source':a,'target':b,'type':c,'required':True} for a,b,c in
       [('委托号','sample_code','text'),('检测项','test_name','text'),
        ('结果','result_value','decimal'),('单位','unit','text'),('时间','measured_at','datetime')]]

def make_rows(n=12,test='拉伸强度',hour=9):
 return [{'委托号':f'JS-20261117-{i:03d}','检测项':test,'结果':str(420+i/10),
          '单位':'MPa','时间':f'2026-11-17T{hour:02d}:00:00+08:00'} for i in range(1,n+1)]

@pytest.fixture(scope='session')
def client():
 Base.metadata.create_all(engine)
 with Session() as s:
  for role in ('admin','operator','viewer'):
   s.add(User(email=role+'@migration.local',role=role,password=hash_password('Migrate2026!')))
  s.add(Batch(name='日班批次',rows=make_rows(),source_file='lab.json',description='d'))
  s.commit()
  bid=s.query(Batch).filter_by(name='日班批次').one().id
  s.add(Mapping(name='标准映射',batch_id=bid,rules=RULES,chunk_size=4,note='n'))
  s.commit();mid=s.query(Mapping).filter_by(name='标准映射').one().id
 with TestClient(app) as c:yield c

def auth(role):
 class U:
  def __init__(self,i,r):self.id=i;self.role=r
 ids={'admin':1,'operator':2,'viewer':3}
 return {'Authorization':'Bearer '+token(U(ids[role],role))}

def run_migration(name='run-A',rows=None):
 """绕过 redis 队列，直接执行 worker 的核心提交逻辑（含原子提交）。"""
 with Session() as s:
  b=s.query(Batch).filter_by(name='日班批次').one()
  m=s.query(Mapping).filter_by(name='标准映射').one()
  snap_b={c.name:getattr(b,c.name) for c in b.__table__.columns}
  snap_m={c.name:getattr(m,c.name) for c in m.__table__.columns}
  source_rows=rows if rows is not None else list(b.rows)
  rules=list(m.rules);chunk=m.chunk_size
  r=Run(name=name,batch_id=b.id,mapping_id=m.id,status='running',
        snapshot={'batch':snap_b,'mapping':snap_m})
  s.add(r);s.commit();rid=r.id
 result=migrate(source_rows,rules,chunk)
 result['backup']=make_backup(result,rid,'日班批次')
 with Session() as s:
  r=s.get(Run,rid)
  for i,row in enumerate(result['accepted_rows']):s.add(TargetRow(run_id=rid,ordinal=i,key_digest=key_digest(row),payload=row))
  r.result=result;r.status='completed';s.commit()
 return rid,result

def q_payload(title='质控失效',test='拉伸强度',frm='2026-11-17T08:00:00+08:00',to='2026-11-17T10:00:00+08:00',reason='设备偏差'):
 return {'title':title,'test_name':test,'measured_from':frm,'measured_to':to,'reason':reason}

# ---------- 1. 登记 + 按检测项/时间标记 ----------
def test_register_and_mark(client):
 rid,result=run_migration('run-mark')
 r=client.post('/quarantines',json=q_payload(),headers=auth('operator'))
 assert r.status_code==200,r.text
 q=r.json();assert q['status']=='draft' and q['code'].startswith('Q-')
 # 待生效不产生标记
 iso=client.get(f'/runs/{rid}/isolation',headers=auth('viewer')).json()
 assert iso['isolation_version']==0 and iso['quarantined_count']==0
 # 生效后标记
 a=client.post(f"/quarantines/{q['id']}/activate",json={},headers=auth('admin'))
 assert a.status_code==200 and a.json()['status']=='active'
 assert len(a.json()['affected'])==12
 iso=client.get(f'/runs/{rid}/isolation',headers=auth('viewer')).json()
 assert iso['isolation_version']==1 and iso['quarantined_count']==12
 assert all(x['quarantined'] and x['quarantine_refs'][0]['id']==q['id'] for x in iso['rows'])
 # 时间窗外不命中：下午的结果
 rid2,_=run_migration('run-pm',rows=make_rows(3,hour=15))
 iso2=client.get(f'/runs/{rid2}/isolation',headers=auth('viewer')).json()
 assert iso2['quarantined_count']==0
 # 检测项不同不命中
 rid3,_=run_migration('run-other',rows=make_rows(3,test='弯曲强度'))
 iso3=client.get(f'/runs/{rid3}/isolation',headers=auth('viewer')).json()
 assert iso3['quarantined_count']==0
 # 用例自清理，避免影响后续用例的全局生效集合
 client.post(f"/quarantines/{q['id']}/revoke",json={},headers=auth('admin'))

# ---------- 2. 多单命中 + 撤销一张其余保留 ----------
def test_overlap_and_partial_revoke(client):
 rid,_=run_migration('run-overlap')
 base=client.post('/quarantines',json=q_payload(title='基础覆盖单'),headers=auth('operator')).json()
 client.post(f"/quarantines/{base['id']}/activate",json={},headers=auth('admin'))
 r2=client.post('/quarantines',json=q_payload(title='第二次质控',frm='2026-11-17T09:00:00+08:00',to='2026-11-17T09:30:00+08:00'),headers=auth('operator')).json()
 client.post(f"/quarantines/{r2['id']}/activate",json={},headers=auth('admin'))
 iso=client.get(f'/runs/{rid}/isolation',headers=auth('viewer')).json()
 first=iso['rows'][0]
 assert len(first['quarantine_refs'])==2  # 同时命中两张
 # 撤销第二张
 client.post(f"/quarantines/{r2['id']}/revoke",json={},headers=auth('admin'))
 iso=client.get(f'/runs/{rid}/isolation',headers=auth('viewer')).json()
 assert iso['quarantined_count']==12  # 第一张仍覆盖全部
 assert all(len(x['quarantine_refs'])==1 and x['quarantine_refs'][0]['id']==base['id'] for x in iso['rows'])
 # 撤销第一张后全部解除
 client.post(f"/quarantines/{base['id']}/revoke",json={},headers=auth('admin'))
 iso=client.get(f'/runs/{rid}/isolation',headers=auth('viewer')).json()
 assert iso['quarantined_count']==0 and not iso['active_quarantines']

# ---------- 3+5. 幂等：重试不重复生效/升版 ----------
def test_idempotent_retry(client):
 rid,_=run_migration('run-idem')
 q=client.post('/quarantines',json=q_payload(title='幂等单'),headers=auth('operator')).json()
 first=client.post(f"/quarantines/{q['id']}/activate",json={},headers=auth('admin')).json()
 again=client.post(f"/quarantines/{q['id']}/activate",json={},headers=auth('admin')).json()
 assert again['status']=='active' and again['activated_at']==first['activated_at']
 v=client.get('/quarantines',headers=auth('viewer')).json()['version']
 client.post(f"/quarantines/{q['id']}/activate",json={},headers=auth('admin'))
 assert client.get('/quarantines',headers=auth('viewer')).json()['version']==v  # 未再升版
 # 撤销幂等
 client.post(f"/quarantines/{q['id']}/revoke",json={},headers=auth('admin'))
 client.post(f"/quarantines/{q['id']}/revoke",json={},headers=auth('admin'))
 lst=client.get('/quarantines',headers=auth('viewer')).json()
 assert lst['version']==v+1
 assert next(x for x in lst['items'] if x['id']==q['id'])['status']=='revoked'
 # 撤销后禁止重新生效
 bad=client.post(f"/quarantines/{q['id']}/activate",json={},headers=auth('admin'))
 assert bad.status_code==409
 # draft 不能撤销
 q2=client.post('/quarantines',json=q_payload(title='草稿单'),headers=auth('operator')).json()
 assert client.post(f"/quarantines/{q2['id']}/revoke",json={},headers=auth('admin')).status_code==409

# ---------- 4. 切换失败回滚，旧版保留 ----------
def test_switch_failure_rolls_back(client,monkeypatch):
 rid,_=run_migration('run-rollback')
 q=client.post('/quarantines',json=q_payload(title='回滚单'),headers=auth('operator')).json()
 client.post(f"/quarantines/{q['id']}/activate",json={},headers=auth('admin'))
 before=client.get('/quarantines',headers=auth('viewer')).json()
 assert before['version']>=1
 import src.config.database as db
 from sqlalchemy.orm import Session as OrmSession
 real_commit=OrmSession.commit
 state={'armed':False}
 def failing_commit(self):
  if state['armed']:
   state['armed']=False;raise RuntimeError('commit failed')
  return real_commit(self)
 monkeypatch.setattr(OrmSession,'commit',failing_commit)
 q2=client.post('/quarantines',json=q_payload(title='将失败单'),headers=auth('operator')).json()
 state['armed']=True  # 仅让生效切换的提交失败
 quiet=TestClient(app,raise_server_exceptions=False)
 resp=quiet.post(f"/quarantines/{q2['id']}/activate",json={},headers=auth('admin'))
 assert resp.status_code==500
 after=client.get('/quarantines',headers=auth('viewer')).json()
 assert after['version']==before['version']  # 版本未前进
 row=next(x for x in after['items'] if x['id']==q2['id'])
 assert row['status']=='draft'  # 旧状态保留
 # 旧版隔离视图仍然完整可读
 iso=client.get(f'/runs/{rid}/isolation',headers=auth('viewer')).json()
 assert iso['isolation_version']==before['version']

# ---------- 6. 并发：切换期间迁移/恢复照常落库，切换完成覆盖新行 ----------
def test_concurrent_writes_covered_by_switch(client):
 rid,result=run_migration('run-concurrent')
 q=client.post('/quarantines',json=q_payload(title='并发单'),headers=auth('operator')).json()
 barrier=threading.Event()
 import src.services.quarantines as qsvc
 orig_affected=qsvc._affected_rows
 def patched_affected(s,qq):
  barrier.set()  # 通知：切换进行中（已持锁）
  time.sleep(0.3)
  return orig_affected(s,qq)
 qsvc._affected_rows=patched_affected
 outcome={}
 def writer():
  barrier.wait(timeout=5)
  # 切换进行期间：旧备份恢复照常落库
  backup=result['backup']
  rr=client.post(f'/runs/{rid}/restore',json={'backup':backup},headers=auth('admin'))
  outcome['restore']=rr.status_code
  # 同时另一批次迁移提交新目标行
  with Session() as s:
   b=s.query(Batch).filter_by(name='日班批次').one();m=s.query(Mapping).filter_by(name='标准映射').one()
   nr=Run(name='run-during-switch',batch_id=b.id,mapping_id=m.id,status='running',snapshot=r_get(s,rid))
   s.add(nr);s.commit();nrid=nr.id
  res=migrate(make_rows(2),RULES,4)
  with Session() as s:
   for i,row in enumerate(res['accepted_rows']):s.add(TargetRow(run_id=nrid,ordinal=i,key_digest=key_digest(row),payload=row))
   s.get(Run,nrid).result=res;s.get(Run,nrid).status='completed';s.commit()
  outcome['new_run']=nrid
 t=threading.Thread(target=writer);t.start()
 resp=client.post(f"/quarantines/{q['id']}/activate",json={},headers=auth('admin'))
 t.join()
 qsvc._affected_rows=orig_affected
 assert resp.status_code==200
 assert outcome['restore']==200  # 恢复未被切换阻塞
 # 切换完成后：期间写入的新运行结果也被同一版规则覆盖
 iso=client.get(f"/runs/{outcome['new_run']}/isolation",headers=auth('viewer')).json()
 assert iso['isolation_version']>=1 and iso['quarantined_count']==2
 assert all(x['quarantined'] for x in iso['rows'])
 # 受影响清单包含切换期间已存在的全部行
 detail=client.get(f"/quarantines/{q['id']}",headers=auth('viewer')).json()
 run_ids={x['run_id'] for x in detail['affected']}
 assert rid in run_ids and outcome['new_run'] in run_ids

def r_get(s,rid):
 r=s.get(Run,rid);return {'batch':r.snapshot['batch'],'mapping':r.snapshot['mapping']}

# ---------- 7. 原结果与备份摘要不受隔离影响 ----------
def test_checksum_unchanged(client):
 rid,result=run_migration('run-checksum')
 before=client.get(f'/runs/{rid}/targets',headers=auth('viewer')).json()
 q=client.post('/quarantines',json=q_payload(title='摘要单'),headers=auth('operator')).json()
 client.post(f"/quarantines/{q['id']}/activate",json={},headers=auth('admin'))
 client.post(f"/quarantines/{q['id']}/revoke",json={},headers=auth('admin'))
 after=client.get(f'/runs/{rid}/targets',headers=auth('viewer')).json()
 assert before['checksum']==after['checksum']==result['checksum']
 assert before['rows']==after['rows']  # 原始目标行零改动
 backup=client.get(f'/runs/{rid}/backup',headers=auth('viewer')).json()
 assert backup['checksum']==result['checksum']
 assert backup['row_hashes']==[digest(x) for x in after['rows']]

# ---------- 8. 权限 ----------
def test_permissions(client):
 assert client.post('/quarantines',json=q_payload(title='权限单'),headers=auth('viewer')).status_code==403
 q=client.post('/quarantines',json=q_payload(title='权限单2'),headers=auth('operator')).json()
 assert client.post(f"/quarantines/{q['id']}/activate",json={},headers=auth('viewer')).status_code==403
 assert client.get('/quarantines',headers=auth('viewer')).status_code==200  # 只读可核对
 assert client.get('/quarantines',headers={}).status_code==401
 # 非法输入
 bad=client.post('/quarantines',json={**q_payload(),'measured_to':'2026-11-17T07:00:00+08:00'},headers=auth('operator'))
 assert bad.status_code==422
 assert client.post('/quarantines',json={**q_payload(),'measured_from':'not-a-date'},headers=auth('operator')).status_code==422
