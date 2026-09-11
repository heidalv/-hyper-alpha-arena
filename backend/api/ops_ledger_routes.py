# -*- coding: utf-8 -*-
"""边际账本 / 费用预算 / 影子车道 运维接口（/api/ops/edge-ledger*，v3 F2）。

只读为主；`/reconcile` 与 `/snapshot` 是幂等的运维动作（回填样本、落一份快照）。
所有口径来自 backend/services/ledger/*，本文件不做任何数值计算。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ops", tags=["ops-ledger"])


def _default_account(db) -> int:
    """默认账户：EDGE_LEDGER_ACCOUNTS 的第一个；否则取权益最大的 paper 账户。"""
    from backend.services.ledger.edge_ledger import default_ledger_accounts
    accts = default_ledger_accounts()
    if accts:
        return accts[0]
    from sqlalchemy import text as _t
    row = db.execute(_t(
        "SELECT account_id FROM paper_balances ORDER BY total_equity DESC NULLS LAST LIMIT 1"
    )).first()
    if not row:
        raise HTTPException(status_code=404, detail="没有 paper 账户")
    return int(row[0])


@router.get("/edge-ledger")
def ops_edge_ledger(
    account_id: Optional[int] = Query(None, description="paper 账户 id；缺省取默认账户"),
    days: int = Query(14, ge=1, le=180),
) -> Dict[str, Any]:
    """边际账本快照：按 tier / 车道 / 出场大类 / 币种的 N、毛、费、净、PF、95% CI，
    附账户真值、费用对账差、trade_facts 覆盖率、短线影子车道晋升门。"""
    from backend.database.connection import SessionLocal
    from backend.services.ledger.edge_ledger import compute_edge_ledger
    from backend.services.ledger.fee_budget import fee_budget_status
    from backend.services.scalp.shadow_mode import shadow_mode_stats

    with SessionLocal() as db:
        acct = int(account_id) if account_id else _default_account(db)
        try:
            snap = compute_edge_ledger(db, acct, days)
        except Exception as exc:
            logger.exception("[ops/edge-ledger] 计算失败 acct=%s", acct)
            raise HTTPException(status_code=500, detail=f"edge_ledger 计算失败: {exc}") from exc
        snap["fee_budget"] = fee_budget_status(db, acct, (snap.get("account_truth") or {}).get("total_equity"))
        snap["shadow_mode"] = shadow_mode_stats()
        try:
            from backend.services.paper_trading_engine import PaperTradingEngine
            snap["trade_fact_write_failures"] = int(getattr(PaperTradingEngine, "_TRADE_FACT_WRITE_FAILURES", 0))
        except Exception:
            snap["trade_fact_write_failures"] = None
        return snap


@router.get("/edge-ledger/history")
def ops_edge_ledger_history(
    account_id: Optional[int] = Query(None),
    limit: int = Query(60, ge=1, le=500),
) -> Dict[str, Any]:
    from backend.database.connection import SessionLocal
    from backend.services.ledger.edge_ledger import snapshot_history

    with SessionLocal() as db:
        acct = int(account_id) if account_id else _default_account(db)
        return {"account_id": acct, "items": snapshot_history(db, acct, limit)}


@router.post("/edge-ledger/snapshot")
def ops_edge_ledger_snapshot(
    account_id: Optional[int] = Query(None),
    days: int = Query(14, ge=1, le=180),
) -> Dict[str, Any]:
    """手动落一份快照（定时任务每日 04:10 也会落）。"""
    from backend.database.connection import SessionLocal
    from backend.services.ledger.edge_ledger import compute_edge_ledger, persist_snapshot

    with SessionLocal() as db:
        acct = int(account_id) if account_id else _default_account(db)
        snap = compute_edge_ledger(db, acct, days)
        sid = persist_snapshot(db, snap)
        return {"ok": sid is not None, "snapshot_id": sid, "total": snap.get("total")}


@router.post("/edge-ledger/reconcile")
def ops_trade_facts_reconcile(
    account_id: Optional[int] = Query(None, description="缺省=全部账户"),
    days: int = Query(7, ge=1, le=90),
) -> Dict[str, Any]:
    """trade_facts 对账回填（幂等）：补齐窗口内缺失的学习样本。"""
    from backend.database.connection import SessionLocal
    from backend.services.ledger.trade_facts_reconcile import backfill_missing_trade_facts

    with SessionLocal() as db:
        return backfill_missing_trade_facts(db, days=days, account_id=account_id)


@router.get("/fee-budget")
def ops_fee_budget(account_id: Optional[int] = Query(None)) -> Dict[str, Any]:
    from backend.database.connection import SessionLocal
    from backend.services.ledger.fee_budget import fee_budget_status

    with SessionLocal() as db:
        acct = int(account_id) if account_id else _default_account(db)
        return fee_budget_status(db, acct)
