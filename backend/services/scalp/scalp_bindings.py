"""交易对策略绑定（候选 → 受监控的运行实例）。

表：pair_strategy_bindings
- enable_candidate：候选通过硬门禁后人工启用，生成绑定（status=running）
- disable_binding：自动熔断或人工下线（paused/stopped）
- 不做灰度：候选未通过门禁不允许 enable。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import text

logger = logging.getLogger(__name__)


def ensure_tables() -> None:
    from backend.core.tenant import system_identity
    from backend.database.connection import SessionLocal

    with system_identity():
        with SessionLocal() as db:
            db.execute(text(
                "CREATE TABLE IF NOT EXISTS pair_strategy_bindings ("
                " id BIGSERIAL PRIMARY KEY,"
                " symbol VARCHAR(32) NOT NULL,"
                " period VARCHAR(8) NOT NULL,"
                " factor_set VARCHAR(16) NOT NULL,"
                " candidate_id BIGINT,"
                " strategy_id VARCHAR(100) NOT NULL,"
                " params_json JSONB NOT NULL DEFAULT '{}'::jsonb,"
                " status VARCHAR(16) NOT NULL DEFAULT 'running',"
                " enabled_at TIMESTAMPTZ NOT NULL DEFAULT now(),"
                " disabled_at TIMESTAMPTZ,"
                " stop_reason TEXT,"
                " created_at TIMESTAMPTZ NOT NULL DEFAULT now())"
            ))
            db.commit()


def _binding_from_row(r) -> Dict[str, Any]:
    return {
        "id": int(r["id"]),
        "symbol": r["symbol"],
        "period": r["period"],
        "factor_set": r["factor_set"],
        "candidate_id": int(r["candidate_id"]) if r["candidate_id"] else None,
        "strategy_id": r["strategy_id"],
        "params": r["params_json"],
        "status": r["status"],
        "enabled_at": r["enabled_at"].isoformat() if r["enabled_at"] else None,
        "disabled_at": r["disabled_at"].isoformat() if r["disabled_at"] else None,
        "stop_reason": r["stop_reason"],
    }


def enable_candidate(candidate_id: int) -> Dict[str, Any]:
    ensure_tables()
    from backend.core.tenant import system_identity
    from backend.database.connection import SessionLocal

    with system_identity():
        with SessionLocal() as db:
            c = db.execute(
                text(
                    "SELECT id, symbol, period, factor_set, params_json, gate_verdict "
                    "FROM pair_strategy_candidates WHERE id = :cid"
                ),
                {"cid": candidate_id},
            ).mappings().first()
            if not c:
                raise ValueError("candidate %s 不存在" % candidate_id)
            if c["gate_verdict"] != "pass":
                raise ValueError("候选未通过硬门禁（%s），禁止启用" % c["gate_verdict"])
            strategy_id = "pair_%s_%s_%s" % (c["symbol"], c["period"], c["factor_set"])
            exists = db.execute(
                text(
                    "SELECT id FROM pair_strategy_bindings "
                    "WHERE strategy_id = :sid AND status = 'running' LIMIT 1"
                ),
                {"sid": strategy_id},
            ).mappings().first()
            if exists:
                raise ValueError("该策略已有运行中绑定 id=%s" % exists["id"])
            row = db.execute(
                text(
                    "INSERT INTO pair_strategy_bindings "
                    "(symbol, period, factor_set, candidate_id, strategy_id, params_json) "
                    "VALUES (:s, :p, :f, :cid, :sid, :params) RETURNING *"
                ),
                {
                    "s": c["symbol"], "p": c["period"], "f": c["factor_set"],
                    "cid": c["id"], "sid": strategy_id,
                    "params": json.dumps(c["params_json"], ensure_ascii=False),
                },
            ).mappings().first()
            db.commit()
            logger.info("[Bindings] 启用候选 %s -> binding %s", candidate_id, row["id"])
            return _binding_from_row(row)


def disable_binding(binding_id: int, reason: str = "manual",
                    status: str = "paused") -> Dict[str, Any]:
    ensure_tables()
    from backend.core.tenant import system_identity
    from backend.database.connection import SessionLocal

    with system_identity():
        with SessionLocal() as db:
            row = db.execute(
                text(
                    "UPDATE pair_strategy_bindings SET status = :st, "
                    "disabled_at = now(), stop_reason = :reason "
                    "WHERE id = :bid RETURNING *"
                ),
                {"st": status, "reason": reason, "bid": binding_id},
            ).mappings().first()
            db.commit()
            if not row:
                raise ValueError("binding %s 不存在" % binding_id)
            logger.info("[Bindings] 下线 binding %s (%s)", binding_id, reason)
            return _binding_from_row(row)


def list_bindings() -> List[Dict[str, Any]]:
    ensure_tables()
    from backend.core.tenant import system_identity
    from backend.database.connection import SessionLocal

    with system_identity():
        with SessionLocal() as db:
            rows = db.execute(
                text("SELECT * FROM pair_strategy_bindings ORDER BY id DESC")
            ).mappings().all()
    return [_binding_from_row(r) for r in rows]
