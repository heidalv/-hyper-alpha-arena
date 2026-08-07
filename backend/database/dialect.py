"""SQL 方言适配层 — 集中管理 SQLite / PostgreSQL 语法差异。

所有需要写原生 SQL 的代码都通过此模块获取适配后的语句，
避免在业务代码中到处做 `if sqlite` 判断。

使用方式:
    from backend.database.dialect import dialect
    sql = f"SELECT * FROM t WHERE created_at >= {dialect.datetime_now_minus(30)}"
"""

import logging
import os

logger = logging.getLogger(__name__)


def _load_database_url() -> str:
    """
    解析 DATABASE_URL，优先级：环境变量 > .env 文件 > 默认值。

    根因修复：dialect 之前只读 os.environ，但脚本/部分服务未加载 .env，
    导致 PG 环境误判为 SQLite，INSERT 用错语法写不进 DB。
    """
    # 1. 环境变量（最高优先级，生产/已初始化场景）
    url = os.environ.get("DATABASE_URL")
    if url:
        return url

    # 2. 从 .env 文件兜底读取（脚本/未加载 .env 的入口）
    from pathlib import Path
    for env_path in [
        Path(__file__).resolve().parents[1] / ".." / ".env",        # repo 根 .env
        Path(__file__).resolve().parents[1] / ".env",                # backend/.env
        Path.cwd() / ".env",                                          # 当前工作目录
    ]:
        env_path = env_path.resolve()
        if env_path.exists():
            try:
                for line in env_path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line.startswith("DATABASE_URL=") and not line.startswith("#"):
                        val = line.split("=", 1)[1].strip().strip('"').strip("'")
                        if val:
                            os.environ["DATABASE_URL"] = val  # 缓存到 environ 供后续复用
                            return val
            except Exception:
                pass

    # 3. 默认值
    return "sqlite:///./data/alpha_arena.db"


_DATABASE_URL: str = _load_database_url()


def _is_sqlite(url: str = "") -> bool:
    return (url or _DATABASE_URL).startswith("sqlite")


def _is_postgresql(url: str = "") -> bool:
    u = (url or _DATABASE_URL).lower()
    return u.startswith("postgresql") or u.startswith("postgres")


class _Dialect:
    """SQL 方言适配器 — 根据 DATABASE_URL 自动选择 SQLite/PostgreSQL 语法。"""

    # ── 检测 ──

    @property
    def is_sqlite(self) -> bool:
        return _is_sqlite()

    @property
    def is_postgresql(self) -> bool:
        return _is_postgresql()

    # ── 日期时间函数 ──

    def datetime_now(self) -> str:
        """当前时间。"""
        if self.is_postgresql:
            return "NOW()"
        return "datetime('now')"

    def datetime_now_minus(self, days: int) -> str:
        """当前时间减 N 天。"""
        if self.is_postgresql:
            return f"NOW() - INTERVAL '{days} days'"
        return f"datetime('now', '-{days} days')"

    def datetime_now_plus(self, days: int) -> str:
        """当前时间加 N 天。"""
        if self.is_postgresql:
            return f"NOW() + INTERVAL '{days} days'"
        return f"datetime('now', '+{days} days')"

    def datetime_now_minus_hours(self, hours: int) -> str:
        """当前时间减 N 小时。"""
        if self.is_postgresql:
            return f"NOW() - INTERVAL '{hours} hours'"
        return f"datetime('now', '-{hours} hours')"

    def datetime_now_minus_param(self) -> str:
        """当前时间减动态天数（使用 :days 参数绑定）。

        用法:  f"... WHERE created_at >= {dialect.datetime_now_minus_param()}"
               params = {"days": 30}
        """
        if self.is_postgresql:
            return "NOW() - :days * INTERVAL '1 day'"
        return "datetime('now', '-' || :days || ' days')"

    # ── INSERT 冲突处理 ──

    def insert_or_ignore_prefix(self) -> str:
        """INSERT 语句前缀（SQLite: INSERT OR IGNORE, PG: INSERT）。

        注意: PG 模式下需要在语句末尾追加 ON CONFLICT DO NOTHING。
        推荐使用 insert_on_conflict_do_nothing() 代替。
        """
        if self.is_postgresql:
            return "INSERT INTO"
        return "INSERT OR IGNORE INTO"

    def on_conflict_do_nothing(self, conflict_cols: str = "") -> str:
        """返回冲突处理子句。SQLite 返回空串，PG 返回 ON CONFLICT 子句。

        Args:
            conflict_cols: 冲突列名，逗号分隔。为空则不指定列（需要表有唯一约束）。
        """
        if self.is_postgresql:
            if conflict_cols:
                return f"ON CONFLICT ({conflict_cols}) DO NOTHING"
            return "ON CONFLICT DO NOTHING"
        return ""

    def insert_on_conflict_do_nothing(
        self, table: str, columns: str, placeholders: str, conflict_cols: str = ""
    ) -> str:
        """生成完整的 INSERT ... ON CONFLICT DO NOTHING 语句。"""
        if self.is_postgresql:
            if conflict_cols:
                return (
                    f"INSERT INTO {table} ({columns}) VALUES ({placeholders}) "
                    f"ON CONFLICT ({conflict_cols}) DO NOTHING"
                )
            return (
                f"INSERT INTO {table} ({columns}) VALUES ({placeholders}) "
                f"ON CONFLICT DO NOTHING"
            )
        return f"INSERT OR IGNORE INTO {table} ({columns}) VALUES ({placeholders})"

    def insert_on_conflict_do_update(
        self,
        table: str,
        columns: str,
        placeholders: str,
        conflict_cols: str,
        update_cols: str,
    ) -> str:
        """生成完整的 INSERT ... ON CONFLICT DO UPDATE 语句（upsert）。

        Args:
            table: 表名
            columns: 插入列，逗号分隔
            placeholders: 插入值占位符，逗号分隔（与 columns 一一对应）
            conflict_cols: 冲突判定列，逗号分隔
            update_cols: 冲突时要更新的列，逗号分隔（会用 ``EXCLUDED.<col>`` 取新值）

        注意: SQLite 用 INSERT OR REPLACE 近似（会重置未列出的列，但对本表 K-line
        场景足够；如需精确保留旧值，SQLite 3.24+ 也支持 ON CONFLICT DO UPDATE，
        但这里为了兼容旧版本 SQLite 统一用 REPLACE 语义）。
        """
        update_col_list = [c.strip() for c in update_cols.split(",") if c.strip()]
        if self.is_postgresql:
            set_clause = ", ".join(f"{c} = EXCLUDED.{c}" for c in update_col_list)
            return (
                f"INSERT INTO {table} ({columns}) VALUES ({placeholders}) "
                f"ON CONFLICT ({conflict_cols}) DO UPDATE SET {set_clause}"
            )
        # SQLite: 3.24+ 支持 ON CONFLICT DO UPDATE；优先用它以保留非冲突列。
        try:
            import sqlite3 as _sqlite3

            if _sqlite3.sqlite_version_info >= (3, 24, 0):
                set_clause = ", ".join(f"{c} = excluded.{c}" for c in update_col_list)
                return (
                    f"INSERT INTO {table} ({columns}) VALUES ({placeholders}) "
                    f"ON CONFLICT ({conflict_cols}) DO UPDATE SET {set_clause}"
                )
        except Exception:
            pass
        return f"INSERT OR REPLACE INTO {table} ({columns}) VALUES ({placeholders})"

    # ── 表存在性检测 ──

    def table_exists_sql(self) -> str:
        """返回检测表是否存在的 SQL。

        参数绑定: :name (表名)
        """
        if self.is_postgresql:
            return (
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema='public' AND table_name=:name"
            )
        return "SELECT name FROM sqlite_master WHERE type='table' AND name=:name"

    def column_info_sql(self, table_name: str) -> str:
        """返回获取表列信息的 SQL。"""
        if self.is_postgresql:
            return (
                "SELECT column_name, data_type, is_nullable "
                f"FROM information_schema.columns WHERE table_name='{table_name}' "
                "ORDER BY ordinal_position"
            )
        return f"PRAGMA table_info({table_name})"

    # ── DDL 主键类型 ──

    def auto_pk(self) -> str:
        """自增主键类型定义。"""
        if self.is_postgresql:
            return "SERIAL PRIMARY KEY"
        return "INTEGER PRIMARY KEY AUTOINCREMENT"

    def big_auto_pk(self) -> str:
        """大整数自增主键类型定义。"""
        if self.is_postgresql:
            return "BIGSERIAL PRIMARY KEY"
        return "INTEGER PRIMARY KEY AUTOINCREMENT"

    # ── 布尔值 ──

    def bool_true(self) -> str:
        if self.is_postgresql:
            return "TRUE"
        return "1"

    def bool_false(self) -> str:
        if self.is_postgresql:
            return "FALSE"
        return "0"

    def bool_type(self) -> str:
        if self.is_postgresql:
            return "BOOLEAN"
        return "INTEGER"

    # ── 工具 ──

    def __repr__(self) -> str:
        mode = "PostgreSQL" if self.is_postgresql else "SQLite"
        return f"<Dialect: {mode}>"


dialect = _Dialect()
