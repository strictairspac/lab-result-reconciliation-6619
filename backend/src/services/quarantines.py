from datetime import datetime,timezone,timedelta
from fastapi import HTTPException
from src.config.database import Session
from src.models.run import Run,TargetRow
from src.models.quarantine import Quarantine,IsolationVersion
from src.utils.serialization import record

def _parse_dt(value,field):
 if not isinstance(value,str) or not value.strip():raise HTTPException(422,field+'不能为空')
 try:
  dt=datetime.fromisoformat(value)
 except ValueError:raise HTTPException(422,field+'时间格式无效，需为ISO8601')
 if dt.tzinfo is None:dt=dt.replace(tzinfo=timezone(timedelta(hours=8)))
 return dt.astimezone(timezone.utc)

def _version_row(s):
 # 仅用于写入事务：不存在则在当前事务内创建（随生效/撤销一起提交）
 row=s.query(IsolationVersion).filter_by(id=1).first()
 if not row:
  row=IsolationVersion(id=1,version=0);s.add(row);s.flush()
 return row

def _current_version(s):
 # 只读路径：尚未发生过切换时版本为 0，不产生任何写入
 row=s.query(IsolationVersion).filter_by(id=1).first()
 return row.version if row else 0

def _utc(dt):
 # SQLite 不保留 tzinfo；生产 PostgreSQL timestamptz 返回带时区时间。统一按 UTC 归一
 if dt.tzinfo is None:return dt.replace(tzinfo=timezone.utc)
 return dt.astimezone(timezone.utc)

def _matches(row,q):
 if row.get('test_name')!=q.test_name:return False
 try:
  measured=datetime.fromisoformat(row['measured_at'])
  if measured.tzinfo is None:measured=measured.replace(tzinfo=timezone.utc)
  measured=measured.astimezone(timezone.utc)
 except (ValueError,TypeError,KeyError):return False
 return _utc(q.measured_from)<=measured<=_utc(q.measured_to)

def _affected_rows(s,q):
 # 切换时对当前所有目标行（含其他迁移/恢复并发写入后已提交的行）重算受影响清单
 hit=[]
 for tr in s.query(TargetRow).order_by(TargetRow.run_id,TargetRow.ordinal):
  if _matches(tr.payload,q):
   hit.append({'run_id':tr.run_id,'key_digest':tr.key_digest,
               'sample_code':tr.payload.get('sample_code'),'test_name':tr.payload.get('test_name'),
               'measured_at':tr.payload.get('measured_at')})
 return hit

def list_items():
 with Session() as s:
  items=[record(q) for q in s.query(Quarantine).order_by(Quarantine.id.desc()).limit(100).all()]
  version=_current_version(s)
  return {'version':version,'items':items}

def detail(item_id):
 with Session() as s:
  q=s.get(Quarantine,item_id)
  if not q:raise HTTPException(404,'未找到隔离单')
  return record(q)

def create(data,user):
 frm=_parse_dt(data.measured_from,'检测开始时间')
 to=_parse_dt(data.measured_to,'检测结束时间')
 if frm>to:raise HTTPException(422,'检测开始时间不能晚于结束时间')
 if not data.test_name.strip():raise HTTPException(422,'检测项不能为空')
 with Session() as s:
  seq=s.query(Quarantine).count()+1
  code=data.code.strip() if data.code and data.code.strip() else f'Q-{datetime.now(timezone.utc).strftime("%Y%m%d")}-{seq:03d}'
  if s.query(Quarantine).filter_by(code=code).first():raise HTTPException(409,'隔离单编号已存在')
  q=Quarantine(code=code,title=data.title.strip(),reason=(data.reason or '').strip(),
               test_name=data.test_name.strip(),measured_from=frm,measured_to=to,
               status='draft',affected=[],created_by=user.email)
  s.add(q);s.commit();s.refresh(q);return record(q)

def activate(item_id,user):
 # 一次性切换：行锁隔离单 + 版本号同行锁事务；失败整体回滚，旧版保留
 with Session() as s:
  q=s.query(Quarantine).filter_by(id=item_id).with_for_update().first()
  if not q:raise HTTPException(404,'未找到隔离单')
  if q.status=='active':return record(q)  # 幂等：重试同一单不重复生效、不重复升版
  if q.status=='revoked':raise HTTPException(409,'隔离单已撤销，不能重新生效，请新建隔离单')
  version=_version_row(s)
  s.refresh(version,with_for_update=True)
  q.affected=_affected_rows(s,q)
  q.status='active';q.activated_at=datetime.now(timezone.utc)
  version.version+=1
  s.commit();s.refresh(q);return record(q)

def revoke(item_id,user):
 with Session() as s:
  q=s.query(Quarantine).filter_by(id=item_id).with_for_update().first()
  if not q:raise HTTPException(404,'未找到隔离单')
  if q.status=='revoked':return record(q)  # 幂等：重复撤销不重复升版
  if q.status=='draft':raise HTTPException(409,'隔离单尚未生效，无需撤销')
  version=_version_row(s)
  s.refresh(version,with_for_update=True)
  q.status='revoked';q.revoked_at=datetime.now(timezone.utc)
  version.version+=1
  s.commit();s.refresh(q);return record(q)

def active_view(s):
 """调用方负责事务/会话；返回 (version, 生效隔离单列表)。单次核对只看到同一版。"""
 version=_current_version(s)
 actives=s.query(Quarantine).filter_by(status='active').order_by(Quarantine.id).all()
 return version,actives

def marks_for_run(run_id):
 with Session() as s:
  if not s.get(Run,run_id):raise HTTPException(404,'未找到对账运行')
  version,actives=active_view(s)
  rows=s.query(TargetRow).filter_by(run_id=run_id).order_by(TargetRow.ordinal).all()
  items=[]
  for tr in rows:
   hits=[{'id':q.id,'code':q.code,'title':q.title} for q in actives if _matches(tr.payload,q)]
   items.append({'row':tr.payload,'quarantined':bool(hits),'quarantine_refs':hits})
  return {'isolation_version':version,
          'active_quarantines':[{'id':q.id,'code':q.code,'title':q.title,'test_name':q.test_name,
                                 'measured_from':_utc(q.measured_from).isoformat(),'measured_to':_utc(q.measured_to).isoformat()} for q in actives],
          'rows':items,'count':len(items),
          'quarantined_count':sum(1 for x in items if x['quarantined'])}
