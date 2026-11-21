from sqlalchemy import Column,Integer,String,JSON,DateTime,func,UniqueConstraint
from src.config.database import Base

class Quarantine(Base):
 __tablename__='quarantines'
 id=Column(Integer,primary_key=True)
 code=Column(String(40),unique=True,nullable=False)
 title=Column(String(120),nullable=False)
 reason=Column(String(500),nullable=False,default='')
 # 谓词：检测项精确匹配 + 检测时间闭区间（UTC ISO8601）；时间窗跨批次/运行全局生效
 test_name=Column(String(160),nullable=False)
 measured_from=Column(DateTime(timezone=True),nullable=False)
 measured_to=Column(DateTime(timezone=True),nullable=False)
 # draft=登记未生效 active=生效中 revoked=已撤销
 status=Column(String(20),nullable=False,default='draft')
 affected=Column(JSON,nullable=False,default=list)
 activated_at=Column(DateTime(timezone=True),nullable=True)
 revoked_at=Column(DateTime(timezone=True),nullable=True)
 created_by=Column(String(120),nullable=False)
 created_at=Column(DateTime(timezone=True),server_default=func.now())
 updated_at=Column(DateTime(timezone=True),server_default=func.now(),onupdate=func.now())

class IsolationVersion(Base):
 __tablename__='isolation_version'
 __table_args__=(UniqueConstraint('id'),)
 id=Column(Integer,primary_key=True)
 # 全局隔离视图版本：每次生效/撤销一次性 +1，读侧只看到完整的一版
 version=Column(Integer,nullable=False,default=0)
 updated_at=Column(DateTime(timezone=True),server_default=func.now())
