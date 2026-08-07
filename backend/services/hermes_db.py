"""Hermes 自进化系统 — 独立数据库层

使用独立 SQLite 文件 data/hermes_evolution.db，通过 proposal_id/strategy_id
字符串关联主库，不改动现有表结构。

全部 6 张表 + DDL + 连接管理。
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Optional

logger = logging.getLogger(__name__)


def _repo_root() -> str:
    """Hyper-Alpha-Arena 项目根目录（与 cwd 无关）。"""
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(os.path.join(here, "..", ".."))


HERMES_DB_PATH = os.path.join(_repo_root(), "data", "hermes_evolution.db")


def resolve_hermes_prompt_path(*parts: str) -> str:
    """解析 Hermes/OpenCode prompt 文件路径（backend/prompts 优先，其次 docs/opencode/prompts）。"""
    root = _repo_root()
    for base in (
        os.path.join(root, "backend", "prompts"),
        os.path.join(root, "docs", "opencode", "prompts"),
    ):
        path = os.path.join(base, *parts)
        if os.path.isfile(path):
            return path
    return os.path.join(root, "backend", "prompts", *parts)

# 单连接锁（SQLite 写入串行化）
_write_lock = threading.Lock()

# ═══════════════════════════════════════════════════════════════════
#  DDL
# ═══════════════════════════════════════════════════════════════════

DDL_STATEMENTS = [
    # 表 1: 提案智慧记录
    """
    CREATE TABLE IF NOT EXISTS proposal_wisdom_records (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        proposal_id     INTEGER NOT NULL,
        outcome         TEXT NOT NULL,       -- improved | degraded | neutral
        focus           TEXT NOT NULL,       -- master_close | frequency | global
        market_condition TEXT,               -- trending_up | trending_down | ranging | volatile
        param_key       TEXT,               -- 被修改的参数名
        param_direction TEXT,               -- increase | decrease
        param_delta_pct REAL,               -- 变更百分比
        pnl_impact      REAL,               -- 每笔 PnL 变化($)
        win_rate_delta  REAL,               -- 胜率变化
        confidence      REAL,               -- 归因置信度
        attribution_json TEXT DEFAULT '{}',
        causal_factors  TEXT,
        context_snapshot TEXT DEFAULT '{}',
        created_at      TEXT DEFAULT (datetime('now')),
        ingested_at     TEXT DEFAULT (datetime('now'))
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_wisdom_outcome ON proposal_wisdom_records(outcome)",
    "CREATE INDEX IF NOT EXISTS idx_wisdom_focus ON proposal_wisdom_records(focus)",
    "CREATE INDEX IF NOT EXISTS idx_wisdom_param ON proposal_wisdom_records(param_key)",
    "CREATE INDEX IF NOT EXISTS idx_wisdom_market ON proposal_wisdom_records(market_condition)",

    # 表 2: 参数效果模式库
    """
    CREATE TABLE IF NOT EXISTS param_effect_patterns (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        param_key       TEXT NOT NULL,
        market_condition TEXT NOT NULL,
        direction       TEXT NOT NULL,       -- increase | decrease
        outcome         TEXT NOT NULL,       -- improved | degraded | neutral
        sample_count    INTEGER DEFAULT 0,
        avg_pnl_impact  REAL DEFAULT 0.0,
        avg_win_rate_delta REAL DEFAULT 0.0,
        confidence_avg  REAL DEFAULT 0.0,
        causal_ratio    REAL DEFAULT 0.0,
        pattern_summary TEXT,
        counter_indicators TEXT,
        last_updated    TEXT DEFAULT (datetime('now')),
        decay_factor    REAL DEFAULT 1.0,
        UNIQUE(param_key, market_condition, direction)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_pep_param ON param_effect_patterns(param_key)",
    "CREATE INDEX IF NOT EXISTS idx_pep_market ON param_effect_patterns(market_condition)",

    # 表 3: Prompt 版本管理
    """
    CREATE TABLE IF NOT EXISTS prompt_versions (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        task_id         TEXT NOT NULL,
        version         TEXT NOT NULL,
        full_text       TEXT NOT NULL,
        manifest_snapshot TEXT DEFAULT '{}',
        change_summary  TEXT,
        change_type     TEXT,                -- manual | auto_optimized | ab_test_winner
        parent_version  TEXT,
        proposals_generated INTEGER DEFAULT 0,
        avg_quality_score REAL DEFAULT 0.0,
        avg_approval_rate REAL DEFAULT 0.0,
        avg_improved_rate REAL DEFAULT 0.0,
        avg_degraded_rate REAL DEFAULT 0.0,
        status          TEXT DEFAULT 'draft', -- draft | active | deprecated | ab_testing
        created_at      TEXT DEFAULT (datetime('now')),
        activated_at    TEXT,
        UNIQUE(task_id, version)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_pv_task ON prompt_versions(task_id)",
    "CREATE INDEX IF NOT EXISTS idx_pv_status ON prompt_versions(status)",

    # 表 4: Prompt A/B 测试
    """
    CREATE TABLE IF NOT EXISTS prompt_ab_tests (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        task_id         TEXT NOT NULL,
        version_a       TEXT NOT NULL,
        version_b       TEXT NOT NULL,
        proposals_a     INTEGER DEFAULT 0,
        proposals_b     INTEGER DEFAULT 0,
        improved_rate_a REAL DEFAULT 0.0,
        improved_rate_b REAL DEFAULT 0.0,
        degraded_rate_a REAL DEFAULT 0.0,
        degraded_rate_b REAL DEFAULT 0.0,
        avg_quality_a   REAL DEFAULT 0.0,
        avg_quality_b   REAL DEFAULT 0.0,
        p_value         REAL,
        winner          TEXT,                -- A | B | tie
        started_at      TEXT DEFAULT (datetime('now')),
        concluded_at    TEXT,
        status          TEXT DEFAULT 'running' -- running | concluded | inconclusive
    )
    """,

    # 表 5: 系统架构进化提案
    """
    CREATE TABLE IF NOT EXISTS architecture_evolution_proposals (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        title           TEXT NOT NULL,
        category        TEXT NOT NULL,        -- new_module | new_config | refactor | new_parameter
        description     TEXT,
        evidence_patterns TEXT,
        related_proposal_ids TEXT,
        feasibility     TEXT,                -- easy | medium | hard | impossible
        expected_impact TEXT,                -- high | medium | low
        implementation_notes TEXT,
        status          TEXT DEFAULT 'pending', -- pending | accepted | rejected | implemented
        created_at      TEXT DEFAULT (datetime('now')),
        reviewed_at     TEXT
    )
    """,

    # 表 6: 策略创生候选
    """
    CREATE TABLE IF NOT EXISTS strategy_genesis_candidates (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        source_wisdom_ids TEXT,
        template_seed   TEXT,
        variant_name    TEXT,
        variant_config  TEXT DEFAULT '{}',
        paper_status    TEXT DEFAULT 'queued', -- queued | incubating | validated | rejected | promoted_live
        paper_pnl       REAL DEFAULT 0.0,
        paper_win_rate  REAL DEFAULT 0.0,
        paper_trades    INTEGER DEFAULT 0,
        paper_days      INTEGER DEFAULT 0,
        viability_score REAL DEFAULT 0.0,
        created_at      TEXT DEFAULT (datetime('now')),
        validated_at    TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_sgc_status ON strategy_genesis_candidates(paper_status)",

    # 表 8: Agent 决策智慧（Swing/Trend 平仓采集，与 proposal_wisdom 分库）
    """
    CREATE TABLE IF NOT EXISTS agent_decision_wisdom (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        agent_type      TEXT NOT NULL,
        trade_id        INTEGER,
        symbol          TEXT NOT NULL,
        side            TEXT,
        regime          TEXT,
        close_reason    TEXT,
        decision_action TEXT,
        confidence      REAL,
        pnl             REAL,
        pnl_pct         REAL,
        outcome         TEXT NOT NULL,
        pattern_key     TEXT,
        context_snapshot TEXT DEFAULT '{}',
        created_at      TEXT DEFAULT (datetime('now'))
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_agent_wisdom_type ON agent_decision_wisdom(agent_type, created_at)",

    # 表 7: 通用任务运行记录 — 持久化所有定时任务（Hermes + OpenCode）的运行状态。
    # 解决 APScheduler 纯内存态导致的"重启后激活周期全部重新计算"bug：
    # 启动时从本表恢复 last_finished_at，据此计算 next_run_time，相位不重置。
    """
    CREATE TABLE IF NOT EXISTS task_run_log (
        job_id               TEXT PRIMARY KEY,
        last_started_at      TEXT,                          -- ISO8601
        last_finished_at     TEXT,                          -- ISO8601
        last_status          TEXT,                          -- ok | error | running
        last_error           TEXT,
        run_count            INTEGER DEFAULT 0,
        last_run_duration_ms INTEGER,
        updated_at           TEXT DEFAULT (datetime('now'))
    )
    """,
]


# ═══════════════════════════════════════════════════════════════════
#  连接管理
# ═══════════════════════════════════════════════════════════════════

def _ensure_dir() -> None:
    os.makedirs(os.path.dirname(HERMES_DB_PATH), exist_ok=True)


def get_hermes_conn() -> sqlite3.Connection:
    """获取 Hermes 数据库连接（自动创建目录 + 表）。"""
    _ensure_dir()
    conn = sqlite3.connect(HERMES_DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def hermes_transaction() -> Iterator[sqlite3.Connection]:
    """Hermes 数据库事务上下文管理器。"""
    conn = get_hermes_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_hermes_db() -> None:
    """初始化 Hermes 数据库：建表（幂等）+ 前向迁移（补缺失列）。"""
    _ensure_dir()
    with _write_lock:
        conn = get_hermes_conn()
        try:
            for stmt in DDL_STATEMENTS:
                try:
                    conn.execute(stmt)
                except sqlite3.OperationalError as e:
                    logger.warning("[HermesDB] DDL warning: %s", e)
            # 前向迁移：对已存在的旧 DB 文件补缺失列（CREATE TABLE IF NOT EXISTS
            # 不会给旧表加列，ALTER TABLE ADD COLUMN 才能补，且需幂等）。
            _run_migrations(conn)
            conn.commit()
            logger.info("[HermesDB] 数据库初始化完成: %s", HERMES_DB_PATH)
        finally:
            conn.close()


def _run_migrations(conn: sqlite3.Connection) -> None:
    """执行全部前向迁移（幂等）。"""
    for table, col, col_ddl in _MIGRATIONS:
        _ensure_column(conn, table, col, col_ddl)


def _ensure_column(conn: sqlite3.Connection, table: str, col: str, col_ddl: str) -> None:
    """幂等补列：仅当 table 存在且缺少 col 时执行 ALTER TABLE ADD COLUMN。

    col_ddl 为 ADD COLUMN 的完整定义（含类型/默认值），例如 'sample_count INTEGER DEFAULT 0'。
    """
    try:
        cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    except sqlite3.OperationalError:
        # 表不存在（CREATE TABLE 尚未建或失败）— 跳过，不报错
        return
    if col in cols:
        return
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_ddl}")
        logger.info("[HermesDB] 迁移: %s 新增列 %s", table, col)
    except sqlite3.OperationalError as e:
        logger.warning("[HermesDB] 迁移失败 %s.%s: %s", table, col, e)


# 前向迁移声明：[(table, col, col_ddl), ...]
# 用于为旧版本 DB 补齐后续版本新增的列。仅列「DDL 之后增量加的列」。
# 注：CREATE TABLE IF NOT EXISTS 不会给已存在的旧表补列，必须靠这里的 ALTER TABLE。
_MIGRATIONS: List[tuple] = [
    # param_effect_patterns 早期版本不含以下列（direction/market_condition/sample_count/decay_factor），
    # 导致旧 DB 文件在 L1 update_pattern_library / get_top_patterns 查询时报
    # "no such column: direction / sample_count"。这里幂等补齐，老库自愈。
    ("param_effect_patterns", "direction",        "TEXT NOT NULL DEFAULT ''"),
    ("param_effect_patterns", "market_condition", "TEXT NOT NULL DEFAULT ''"),
    ("param_effect_patterns", "sample_count",     "INTEGER DEFAULT 0"),
    ("param_effect_patterns", "decay_factor",     "REAL DEFAULT 1.0"),
]


def _ensure_hermes_ready() -> None:
    """读/写前确保表结构与迁移已应用（幂等）。"""
    init_hermes_db()


def hermes_execute(sql: str, params: tuple = ()) -> int:
    """执行写操作，返回 lastrowid。"""
    _ensure_hermes_ready()
    with _write_lock:
        conn = get_hermes_conn()
        try:
            cur = conn.execute(sql, params)
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()


def hermes_executemany(sql: str, params_list: List[tuple]) -> None:
    """批量写操作。"""
    _ensure_hermes_ready()
    with _write_lock:
        conn = get_hermes_conn()
        try:
            conn.executemany(sql, params_list)
            conn.commit()
        finally:
            conn.close()


def hermes_fetchall(sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
    """执行查询，返回字典列表。"""
    _ensure_hermes_ready()
    conn = get_hermes_conn()
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def hermes_fetchone(sql: str, params: tuple = ()) -> Optional[Dict[str, Any]]:
    rows = hermes_fetchall(sql, params)
    return rows[0] if rows else None


# ═══════════════════════════════════════════════════════════════════
#  task_run_log 持久化辅助 — 供持久化 Tracker 使用
# ═══════════════════════════════════════════════════════════════════

def upsert_task_run(job_id: str, **fields: Any) -> None:
    """UPSERT task_run_log 一行（按 job_id 主键）。

    fields 仅传本次需更新的列；未传的列保留旧值。updated_at 自动刷新。
    用 INSERT ... ON CONFLICT DO UPDATE 实现（SQLite 3.24+）。
    """
    if not fields:
        return
    # 允许的列白名单（防注入）
    allowed = {"last_started_at", "last_finished_at", "last_status",
               "last_error", "run_count", "last_run_duration_ms"}
    clean = {k: v for k, v in fields.items() if k in allowed}
    if not clean:
        return
    cols = list(clean.keys())
    # INSERT 侧：job_id 用 ?，其余列也用 ?（这样 excluded.<col> 能引用到值）
    col_list = ", ".join(["job_id"] + cols)
    placeholders = ", ".join(["?"] * (len(cols) + 1))
    vals: list = [job_id] + [clean[c] for c in cols]
    upd = ", ".join([f"{c}=excluded.{c}" for c in cols])
    sql = (
        f"INSERT INTO task_run_log ({col_list}) VALUES ({placeholders}) "
        f"ON CONFLICT(job_id) DO UPDATE SET {upd}, updated_at=datetime('now')"
    )
    hermes_execute(sql, tuple(vals))


def increment_task_run_count(job_id: str) -> None:
    """run_count 自增 1（ON CONFLICT 时；首次插入为 1）。"""
    hermes_execute(
        "INSERT INTO task_run_log (job_id, run_count) VALUES (?, 1) "
        "ON CONFLICT(job_id) DO UPDATE SET run_count = run_count + 1, "
        "updated_at=datetime('now')",
        (job_id,),
    )


def get_task_run(job_id: str) -> Optional[Dict[str, Any]]:
    """读取单个任务的运行记录。"""
    return hermes_fetchone("SELECT * FROM task_run_log WHERE job_id = ?", (job_id,))


def get_all_task_runs() -> Dict[str, Dict[str, Any]]:
    """读取全部任务运行记录，返回 {job_id: row}。"""
    rows = hermes_fetchall("SELECT * FROM task_run_log", ())
    return {r["job_id"]: r for r in rows}


# ═══════════════════════════════════════════════════════════════════
#  辅助：获取主库连接（通过 SessionLocal）
# ═══════════════════════════════════════════════════════════════════

def get_main_session():
    """获取主数据库 Session（用于查询 opencode_evolution_proposals 等表）。"""
    from backend.database.connection import SessionLocal
    return SessionLocal()
