"""任务心跳（可观测性第一层）。

表：experiment_heartbeat
- 每个关键任务/车道周期性 touch(task_id)；
- 前端按 last_ok_at 判断正常/滞后/中断；
- 导出到 Obsidian 时把超时任务标红。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy import text

logger = logging.getLogger(__name__)


_ensured = False
_ensure_lock = __import__("threading").Lock()


def ensure_table() -> None:
    """建表（每进程仅一次）。

    [2026-09-01 F32] 旧实现 get_heartbeats/touch 每次都执行 CREATE TABLE IF NOT EXISTS：
    DDL 需要 ACCESS EXCLUSIVE 锁，而 heartbeat 写入每秒都在发生 → 每次读取都要
    排队等锁（实测 ops_heartbeats 单次 1.6s，是 ops 页 fresh 重算的主成本之一）。
    改为进程内一次性建表后，读取变纯 SELECT（毫秒级）。
    """
    global _ensured
    if _ensured:
        return
    with _ensure_lock:
        if _ensured:
            return
        try:
            from backend.database.connection import SessionLocal
            from backend.core.tenant import system_identity

            with system_identity():
                with SessionLocal() as db:
                    db.execute(text(
                        "CREATE TABLE IF NOT EXISTS experiment_heartbeat ("
                        " task_id VARCHAR(80) PRIMARY KEY,"
                        " last_ok_at TIMESTAMPTZ NOT NULL,"
                        " last_status VARCHAR(16) NOT NULL DEFAULT 'ok',"
                        " detail_json JSONB)"
                    ))
                    db.commit()
            _ensured = True
        except Exception as e:
            logger.debug("[Heartbeat] ensure_table 失败: %s", e)


def touch(task_id: str, status: str = "ok",
          detail: Optional[Dict[str, Any]] = None) -> None:
    try:
        ensure_table()
        from backend.database.connection import SessionLocal
        from backend.core.tenant import system_identity

        with system_identity():
            with SessionLocal() as db:
                db.execute(
                    text(
                        "INSERT INTO experiment_heartbeat "
                        "(task_id, last_ok_at, last_status, detail_json) "
                        "VALUES (:tid, now(), :status, :detail) "
                        "ON CONFLICT (task_id) DO UPDATE SET "
                        " last_ok_at = now(), last_status = :status, detail_json = :detail"
                    ),
                    {
                        "tid": task_id,
                        "status": status,
                        "detail": json.dumps(detail or {}, ensure_ascii=False),
                    },
                )
                db.commit()
    except Exception as e:
        logger.debug("[Heartbeat] %s touch 失败: %s", task_id, e)


def get_heartbeats() -> Dict[str, Dict[str, Any]]:
    # [F32] 不在此处重复建表（每进程一次性），避免 DDL 锁排队
    from backend.database.connection import SessionLocal
    from backend.core.tenant import system_identity

    out: Dict[str, Dict[str, Any]] = {}
    with system_identity():
        with SessionLocal() as db:
            rows = db.execute(
                text(
                    "SELECT task_id, last_ok_at, last_status, detail_json "
                    "FROM experiment_heartbeat ORDER BY task_id"
                )
            ).mappings().all()
    for r in rows:
        out[str(r["task_id"])] = {
            "last_ok_at": r["last_ok_at"].isoformat() if r["last_ok_at"] else None,
            "last_status": r["last_status"],
            "detail": r["detail_json"],
        }
    return out
