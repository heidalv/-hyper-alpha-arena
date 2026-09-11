# -*- coding: utf-8 -*-
"""套利基础设施 API（/api/arb-infra/*，v3 方向 5，p2-arb-infra）。

只读：
  GET  /api/arb-infra/status
  GET  /api/arb-infra/scorecard
  GET  /api/arb-infra/fund-transfer/status
  GET  /api/arb-infra/fund-transfer/ledger
  GET  /api/arb-infra/carry-small/status

写（需 X-Risk-Token 或本机）：
  POST /api/arb-infra/scorecard/refresh
  POST /api/arb-infra/fund-transfer          默认 dry-run
  POST /api/arb-infra/carry-small/run
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, HTTPException, Query, Request

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/arb-infra", tags=["arb-infra"])


def _authorize(request: Request, token_in_body: Optional[str] = None) -> None:
    expected = (os.getenv("RISK_COMMAND_TOKEN", "") or "").strip()
    provided = (request.headers.get("X-Risk-Token") or token_in_body or "").strip()
    if expected:
        if provided != expected:
            raise HTTPException(status_code=401, detail="invalid token")
        return
    host = (request.client.host if request.client else "") or ""
    if host not in ("127.0.0.1", "::1", "localhost", "testclient"):
        raise HTTPException(status_code=403, detail="RISK_COMMAND_TOKEN 未配置，仅允许本机调用")


@router.get("/status")
def arb_infra_status() -> Dict[str, Any]:
    from backend.services.arbitrage.fund_transfer import status as ft_status
    from backend.services.arbitrage.scorecard import latest_scorecard
    from backend.services.cashflow.carry_small import carry_small_status
    from backend.services.rebate_arb.strategy_runtime_registry import get_runtime_spec

    sc = latest_scorecard()
    sdn = get_runtime_spec("SDN")
    return {
        "sdn_in_runtime": sdn is not None,
        "sdn": {
            "strategy_id": sdn.strategy_id if sdn else None,
            "paper_auto_executable": bool(sdn.paper_auto_executable) if sdn else False,
            "summary": sdn.summary if sdn else None,
        },
        "scorecard": {
            "has_latest": sc is not None,
            "gate_passed": bool((sc or {}).get("promotion_gate", {}).get("passed")) if sc else None,
            "kpi": (sc or {}).get("kpi") if sc else None,
        },
        "fund_transfer": ft_status(),
        "carry_small": carry_small_status(),
        "note": "cross_market_transfer.py 是知识迁移；资金划转见 FundTransferService",
    }


@router.get("/scorecard")
def get_scorecard(days: int = Query(30, ge=1, le=365),
                  refresh: bool = Query(False)) -> Dict[str, Any]:
    from backend.services.arbitrage.scorecard import compute_scorecard, latest_scorecard
    if refresh:
        return compute_scorecard(days=days)
    sc = latest_scorecard()
    if sc and int(sc.get("days") or 0) == days:
        return sc
    return compute_scorecard(days=days)


@router.post("/scorecard/refresh")
def refresh_scorecard(request: Request, body: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    _authorize(request, body.get("token"))
    from backend.services.arbitrage.scorecard import compute_scorecard
    return compute_scorecard(days=int(body.get("days") or 30))


@router.get("/fund-transfer/status")
def fund_transfer_status() -> Dict[str, Any]:
    from backend.services.arbitrage.fund_transfer import status
    return status()


@router.get("/fund-transfer/ledger")
def fund_transfer_ledger(limit: int = Query(50, ge=1, le=500)) -> Dict[str, Any]:
    from backend.services.arbitrage.fund_transfer import list_transfers
    rows = list_transfers(limit=limit)
    return {"n": len(rows), "transfers": rows}


@router.post("/fund-transfer")
async def fund_transfer(request: Request, body: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    """发起划转。默认 dry-run（FUND_TRANSFER_LIVE=false）。

    body: account_id, exchange, amount, asset?, transfer_type?, reason?, dry_run?
    真划转需 FUND_TRANSFER_ENABLED + FUND_TRANSFER_LIVE，且适配器实现 transfer。
    """
    _authorize(request, body.get("token"))
    from backend.services.arbitrage.fund_transfer import TransferRequest, transfer_async

    try:
        amount = float(body.get("amount") or 0)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="amount 无效")
    account_id = int(body.get("account_id") or 0)
    exchange = str(body.get("exchange") or "").strip().lower()
    if account_id <= 0 or not exchange:
        raise HTTPException(status_code=400, detail="需要 account_id 与 exchange")

    req = TransferRequest(
        account_id=account_id,
        exchange=exchange,
        amount=amount,
        asset=str(body.get("asset") or "USDT"),
        transfer_type=str(body.get("transfer_type") or "MAIN_UMFUTURE"),
        reason=str(body.get("reason") or "")[:200],
        dry_run=body.get("dry_run"),
    )

    client = None
    # dry-run 不需要 client；真划转才建连
    from backend.services.arbitrage.fund_transfer import live_enabled, service_enabled
    need_live = service_enabled() and (req.dry_run is False or (req.dry_run is None and live_enabled()))
    if need_live:
        try:
            from backend.database.connection import SessionLocal
            from backend.database.models import Account
            from backend.services.exchange.exchange_manager import exchange_manager
            db = SessionLocal()
            try:
                acct = db.query(Account).filter(Account.id == account_id).first()
            finally:
                db.close()
            if acct is None:
                raise HTTPException(status_code=404, detail="账户不存在")
            client = await exchange_manager.get_client(acct)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"建连失败: {exc}") from exc

    result = await transfer_async(client, req)
    return result.to_dict()


@router.get("/carry-small/status")
def carry_small_status_api() -> Dict[str, Any]:
    from backend.services.cashflow.carry_small import carry_small_status
    return carry_small_status()


@router.post("/carry-small/run")
def carry_small_run(request: Request, body: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    _authorize(request, body.get("token"))
    from backend.services.cashflow.carry_small import run_carry_small
    return run_carry_small(force_refresh_gate=bool(body.get("force_refresh_gate")))
