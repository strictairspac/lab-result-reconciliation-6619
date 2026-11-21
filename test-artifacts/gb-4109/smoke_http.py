"""HTTP 全链路冒烟：不依赖 redis 队列，迁移直接走 worker 核心逻辑。"""
import os
os.environ.setdefault('JWT_SECRET','smoke-secret')
os.environ.setdefault('DATABASE_URL','sqlite:////tmp/gb4109_smoke.db')
os.environ.setdefault('REDIS_URL','redis://localhost:6379/0')
if os.path.exists('/tmp/gb4109_smoke.db'):os.remove('/tmp/gb4109_smoke.db')
from sqlalchemy.orm import Query
Query.with_for_update=lambda self,*a,**k:self
from fastapi.testclient import TestClient
from src.config.database import Base,engine,Session
from src.models.identity import User
from src.models.batch import Batch
from src.models.mapping import Mapping
from src.utils.security import hash_password
from src.main import app
from test_quarantine import run_migration,auth,q_payload,RULES,make_rows

Base.metadata.create_all(engine)
with Session() as s:
 s.add(User(email='admin@migration.local',role='admin',password=hash_password('Migrate2026!')))
 s.add(Batch(name='日班批次',rows=make_rows(),source_file='lab.json',description='d'))
 s.commit()
 bid=s.query(Batch).filter_by(name='日班批次').one().id
 s.add(Mapping(name='标准映射',batch_id=bid,rules=RULES,chunk_size=4,note='n'))
 s.commit()
with TestClient(app) as c:
 login=c.post('/auth/login',json={'email':'admin@migration.local','password':'Migrate2026!'})
 assert login.status_code==200
 h=auth('admin')
 rid,result=run_migration('smoke-run')
 # 登记 + 生效
 q=c.post('/quarantines',json=q_payload(),headers=h).json()
 assert q['status']=='draft'
 act=c.post(f"/quarantines/{q['id']}/activate",json={},headers=h).json()
 assert act['status']=='active' and len(act['affected'])==12
 iso=c.get(f'/runs/{rid}/isolation',headers=h).json()
 print('version',iso['isolation_version'],'quarantined',iso['quarantined_count'],'/',iso['count'])
 assert iso['quarantined_count']==12 and iso['isolation_version']==1
 # 备份恢复后摘要一致，隔离标记仍覆盖
 backup=c.get(f'/runs/{rid}/backup',headers=h).json()
 rr=c.post(f'/runs/{rid}/restore',json={'backup':backup},headers=h).json()
 assert rr['verified'] is True
 iso2=c.get(f'/runs/{rid}/isolation',headers=h).json()
 assert iso2['quarantined_count']==12
 targets=c.get(f'/runs/{rid}/targets',headers=h).json()
 assert targets['checksum']==result['checksum']
 # 撤销
 rev=c.post(f"/quarantines/{q['id']}/revoke",json={},headers=h).json()
 assert rev['status']=='revoked'
 iso3=c.get(f'/runs/{rid}/isolation',headers=h).json()
 assert iso3['quarantined_count']==0 and iso3['isolation_version']==2
 # 幂等重试
 again=c.post(f"/quarantines/{q['id']}/revoke",json={},headers=h).json()
 assert again['revoked_at']==rev['revoked_at']
 v=c.get('/quarantines',headers=h).json()['version']
 c.post(f"/quarantines/{q['id']}/revoke",json={},headers=h)
 assert c.get('/quarantines',headers=h).json()['version']==v
 print('SMOKE OK')
