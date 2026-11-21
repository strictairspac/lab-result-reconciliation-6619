# Schema v1
SQLAlchemy metadata creates users/audits/batches/mappings/runs/target_rows. Unique natural names, foreign keys, and target unique(run_id,key_digest) enforce linkage. Successful workers atomically commit accepted target rows; cancelled/failed runs do not persist partial targets. Restore replaces a run's target set under a run row lock and checks the logical backup checksum before commit.

# Schema v2 - 隔离单
新增 quarantines 与 isolation_version 两表，由 SQLAlchemy metadata 一并创建（`Base.metadata.create_all`），不改动既有 target_rows/runs 结构与备份摘要。
- quarantines：隔离单主表。code 唯一；谓词为 test_name 精确匹配 + measured_from/measured_to 闭区间（UTC）；status 走 draft→active→revoked，affected 保存切换时刻命中的目标行快照（run_id/key_digest/自然键/检测时间），activated_at/revoked_at 留痕。
- isolation_version：单行(id=1)全局隔离视图版本号。每次生效/撤销在同一事务内对隔离单 `SELECT ... FOR UPDATE` 后将 version+1；读侧在同一会话读取 version 与全部 active 隔离单，单次核对只看到完整一版。事务失败整体回滚，旧版保留。
- 目标行不写隔离标记：命中关系在读取时按当前生效版动态计算，故迁移提交与备份恢复照常并发落库；切换完成后期间写入的新行同样被同一版规则覆盖，原结果与备份 checksum/逐行摘要保持不变。
