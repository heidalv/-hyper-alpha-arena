# 数据库生产部署指南 (Database Production Guide)

## 当前状态

项目默认使用 **SQLite + WAL 模式**，适合单实例开发和纸面仿真。

## 切换到 PostgreSQL

### 1. 环境变量

```bash
DATABASE_URL=postgresql+psycopg2://user:password@host:5432/dbname
```

修改 `backend/services/database_service.py` 中 `DATABASE_URL` 读取逻辑即可。

### 2. 连接池建议

```python
from sqlalchemy import create_engine

engine = create_engine(
    DATABASE_URL,
    pool_size=10,           # 常驻连接数
    max_overflow=20,        # 突发额外连接
    pool_timeout=30,        # 等待连接超时（秒）
    pool_recycle=1800,      # 连接回收（秒），防 PG 主动断开
    pool_pre_ping=True,     # 使用前检测连接存活
)
```

### 3. SQLite → PostgreSQL 差异清单

| 特性 | SQLite | PostgreSQL |
|------|--------|------------|
| 自增 ID | `INTEGER PRIMARY KEY` | `SERIAL` / `BIGSERIAL` |
| 布尔类型 | `INTEGER 0/1` | `BOOLEAN` |
| 时间戳 | `TIMESTAMP` (naive) | `TIMESTAMPTZ` (推荐带时区) |
| JSON 存储 | `TEXT` + JSON.parse | `JSONB` (原生索引) |
| 并发写 | WAL 模式单写 | MVCC 多写 |
| 锁超时 | `_safe_commit` 重试 | `statement_timeout` / `idle_in_transaction_timeout` |
| 备份 | 文件拷贝 | `pg_dump` / WAL 归档 |
| `_safe_commit` 重试 | 必需（SQLite 锁竞争常见） | PG 行级锁下大部分场景不需要，但仍建议保留防死锁 |

### 4. 迁移步骤

1. 安装依赖：`pip install psycopg2-binary`
2. 创建数据库和用户
3. 首次启动时 SQLAlchemy `create_all()` 自动建表
4. 如需迁移现有数据，使用 `pgloader` 或手动导出/导入

### 5. 生产运维

- **连接监控**：`pg_stat_activity` 视图
- **慢查询**：`pg_stat_statements` 扩展
- **自动清理**：确保 `autovacuum` 开启（默认开启）
- **备份策略**：每日 `pg_dump` + WAL 归档实现 PITR

## 不做事项

- 本文档不包含迁移脚本；`create_all()` 足以创建表结构
- 不涉及分库分表或读写分离设计
