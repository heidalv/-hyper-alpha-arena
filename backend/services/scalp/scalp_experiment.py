"""scalp 单变量实验登记（M3 基础设施，只写登记表，不改交易）。

表：scalp_experiment_log
- 任何参数/行为改动必须先 create，再 started，再 completed；
- 禁止把多个改动打包进一个 experiment_id；
- 回滚时写 status=rolled_back 与回滚说明。
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy import text

logger = logging.getLogger(__name__)


def ensure_table() -> None:
    from backend.database.connection import SessionLocal
    from backend.core.tenant import system_identity

    with system_identity():
        with SessionLocal() as db:
            db.execute(text(
                "CREATE TABLE IF NOT EXISTS scalp_experiment_log ("
                " id BIGSERIAL PRIMARY KEY,"
                " experiment_id VARCHAR(80) NOT NULL UNIQUE,"
                " change_desc TEXT NOT NULL,"
                " switches_json JSONB NOT NULL DEFAULT '{}'::jsonb,"
                " baseline_json JSONB,"
                " status VARCHAR(24) NOT NULL DEFAULT 'planned',"
                " started_at TIMESTAMPTZ,"
                " completed_at TIMESTAMPTZ,"
                " result_json JSONB,"
                " verdict VARCHAR(24),"
                " rollback_note TEXT,"
                " owner VARCHAR(64),"
                " created_at TIMESTAMPTZ NOT NULL DEFAULT now())"
            ))
            db.commit()


def log_experiment(
    experiment_id: str,
    change_desc: str,
    switches: Optional[Dict[str, Any]] = None,
    baseline: Optional[Dict[str, Any]] = None,
    owner: str = "codex",
) -> None:
    ensure_table()
    from backend.database.connection import SessionLocal
    from backend.core.tenant import system_identity

    with system_identity():
        with SessionLocal() as db:
            db.execute(
                text(
                    "INSERT INTO scalp_experiment_log "
                    "(experiment_id, change_desc, switches_json, baseline_json, owner) "
                    "VALUES (:eid, :desc, :sw, :bl, :owner) "
                    "ON CONFLICT (experiment_id) DO NOTHING"
                ),
                {
                    "eid": experiment_id,
                    "desc": change_desc,
                    "sw": json.dumps(switches or {}, ensure_ascii=False),
                    "bl": json.dumps(baseline or {}, ensure_ascii=False),
                    "owner": owner,
                },
            )
            db.commit()
    logger.info("[ScalpExperiment] registered %s", experiment_id)


def set_status(experiment_id: str, status: str, result: Optional[Dict[str, Any]] = None,
               verdict: Optional[str] = None, rollback_note: Optional[str] = None) -> None:
    ensure_table()
    from backend.database.connection import SessionLocal
    from backend.core.tenant import system_identity

    now = datetime.now(timezone.utc)
    with system_identity():
        with SessionLocal() as db:
            fields = ["status = :status"]
            params: Dict[str, Any] = {"eid": experiment_id, "status": status}
            if status == "started":
                fields.append("started_at = :now")
                params["now"] = now
            if status in ("completed", "failed", "rolled_back"):
                fields.append("completed_at = :now")
                params["now"] = now
            if result is not None:
                fields.append("result_json = :res")
                params["res"] = json.dumps(result, ensure_ascii=False)
            if verdict is not None:
                fields.append("verdict = :verdict")
                params["verdict"] = verdict
            if rollback_note is not None:
                fields.append("rollback_note = :note")
                params["note"] = rollback_note
            db.execute(
                text(
                    "UPDATE scalp_experiment_log SET %s WHERE experiment_id = :eid"
                    % ", ".join(fields)
                ),
                params,
            )
            db.commit()
    logger.info("[ScalpExperiment] %s -> %s", experiment_id, status)


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="scalp 实验登记")
    ap.add_argument("--create", required=True, help="experiment_id")
    ap.add_argument("--desc", required=True, help="改动说明")
    ap.add_argument("--switches", default="{}", help="JSON 开关")
    ap.add_argument("--baseline", default="{}", help="JSON 基线")
    args = ap.parse_args()
    try:
        sw = json.loads(args.switches)
        bl = json.loads(args.baseline)
    except Exception as e:
        print("JSON 解析失败:", e)
        sys.exit(1)
    log_experiment(args.create, args.desc, sw, bl)
    print("ok")
