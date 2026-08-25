"""M0 认知数据层建表（U2-2，2026-08-25）——统一计划 V2 §U2。

brain_* 六表 + strategy_trades 两个可空列（thesis_id / decision_source）。
全部幂等：CREATE TABLE checkfirst=True；ADD COLUMN 先查 information_schema。
运行: .venv/Scripts/python.exe backend/database/migrations/add_brain_m0_tables.py
"""
from __future__ import annotations

import logging
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from sqlalchemy import (
    Boolean, Column, DateTime, Float, Integer, MetaData, String, Table, Text,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _engine():
    from backend.database.connection import SessionLocal
    db = SessionLocal()
    try:
        return db.get_bind(), db
    except Exception:
        db.close()
        raise


def _add_column_if_missing(conn, table: str, column: str, ddl: str) -> None:
    from sqlalchemy import text
    row = conn.execute(
        text("SELECT 1 FROM information_schema.columns "
             "WHERE table_name = :t AND column_name = :c"),
        {"t": table, "c": column},
    ).first()
    if row is None:
        conn.execute(text(ddl))
        logger.info("ALTER %s ADD COLUMN %s", table, column)
    else:
        logger.info("skip %s.%s (已存在)", table, column)


def main() -> None:
    bind, db = _engine()
    conn = db.connection()
    try:
        # 穿透 RLS（与其它迁移一致）
        try:
            conn.exec_driver_sql("SET app.is_admin = 'on'")
        except Exception:
            pass
        meta = MetaData()

        Table("brain_theses", meta,
              Column("id", Integer, primary_key=True, autoincrement=True),
              Column("thesis_id", String(64), index=True),
              Column("session_id", String(64), index=True),
              Column("symbol", String(32), index=True),
              Column("tier", String(16)),
              Column("direction", String(16)),
              Column("llm_conviction", Integer, default=0),
              Column("thesis_summary", Text),
              Column("invalidation_json", Text),
              Column("missing_evidence_json", Text),
              Column("recommend_open", Boolean),
              Column("should_close", Boolean),
              Column("source", String(32), default="thesis_shadow"),
              Column("created_at", DateTime),
              Column("updated_at", DateTime))

        Table("brain_episodes", meta,
              Column("id", Integer, primary_key=True, autoincrement=True),
              Column("trade_id", Integer, index=True),
              Column("thesis_id", String(64), index=True),
              Column("symbol", String(32), index=True),
              Column("decision_source", String(32)),
              Column("close_reason", String(128)),
              Column("pnl", Float, default=0.0),
              Column("fee", Float, default=0.0),
              Column("net", Float, default=0.0),
              Column("win", Boolean),
              Column("attribution_json", Text),
              Column("created_at", DateTime))

        Table("brain_attribution", meta,
              Column("id", Integer, primary_key=True, autoincrement=True),
              Column("position_id", Integer, index=True),
              Column("src", String(32), index=True),
              Column("nature", String(32)),
              Column("symbol", String(32), index=True),
              Column("pnl", Float, default=0.0),
              Column("fee", Float, default=0.0),
              Column("net", Float, default=0.0),
              Column("win", Boolean),
              Column("close_reason", String(128)),
              Column("tier", String(16)),
              Column("created_at", DateTime))

        Table("brain_lessons", meta,
              Column("id", Integer, primary_key=True, autoincrement=True),
              Column("lesson_text", Text),
              Column("embedding_ref", String(128)),
              Column("source_episode_id", Integer),
              Column("confirm_count", Integer, default=0),
              Column("contradict_count", Integer, default=0),
              Column("status", String(16), default="active"),
              Column("created_at", DateTime),
              Column("updated_at", DateTime))

        Table("brain_agent_calibration", meta,
              Column("id", Integer, primary_key=True, autoincrement=True),
              Column("agent_id", String(64), index=True),
              Column("confidence_bucket", String(16)),
              Column("hits", Integer, default=0),
              Column("total", Integer, default=0),
              Column("updated_at", DateTime))

        Table("brain_research_tasks", meta,
              Column("id", Integer, primary_key=True, autoincrement=True),
              Column("task_type", String(32)),
              Column("source_signal", String(64)),
              Column("symbol", String(32), index=True),
              Column("status", String(16), default="pending"),
              Column("budget", Float, default=0.0),
              Column("roi", Float),
              Column("created_at", DateTime),
              Column("updated_at", DateTime))

        meta.create_all(bind=bind, checkfirst=True)
        logger.info("brain_* 六表就绪（checkfirst 幂等）")

        _add_column_if_missing(
            conn, "strategy_trades", "thesis_id",
            "ALTER TABLE strategy_trades ADD COLUMN thesis_id VARCHAR(64)",
        )
        _add_column_if_missing(
            conn, "strategy_trades", "decision_source",
            "ALTER TABLE strategy_trades ADD COLUMN decision_source VARCHAR(32)",
        )
        db.commit()
        logger.info("strategy_trades 新列就绪")
    finally:
        try:
            db.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
