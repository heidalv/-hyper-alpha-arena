# -*- coding: utf-8 -*-
"""晋升与放量 API（/api/promotion/*，v3 p3-promotion）。"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, HTTPException, Query, Request

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/promotion", tags=["promotion"])


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
def promotion_status() -> Dict[str, Any]:
    from backend.services.allocation.capital_allocator import (
        latest_allocation, promotion_frozen,
    )
    from backend.research.paid_data_decision import latest_decision
    from backend.services.trend_e1_f4_gate import evaluate_f4_gate
    from backend.services.cashflow.funding_tail_harvest import latest as tail_latest

    return {
        "freeze": promotion_frozen(),
        "allocation": latest_allocation(),
        "f4_gate": evaluate_f4_gate(persist=False),
        "paid_data": latest_decision(),
        "funding_tail": tail_latest(),
    }


@router.get("/allocation")
def get_allocation(refresh: bool = Query(False)) -> Dict[str, Any]:
    from backend.services.allocation.capital_allocator import allocate, latest_allocation
    if refresh:
        return allocate(persist=True)
    return latest_allocation() or allocate(persist=True)


@router.post("/allocation/refresh")
def refresh_allocation(request: Request, body: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    _authorize(request, body.get("token"))
    from backend.services.allocation.capital_allocator import allocate
    return allocate(persist=True)


@router.post("/ladder/promote")
def ladder_promote(request: Request, body: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    _authorize(request, body.get("token"))
    sid = str(body.get("strategy_id") or "").strip()
    if not sid:
        raise HTTPException(status_code=400, detail="需要 strategy_id")
    from backend.services.allocation.capital_allocator import promote_strategy
    return promote_strategy(sid, force=bool(body.get("force")))


@router.post("/ladder/advance")
def ladder_advance(request: Request, body: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    _authorize(request, body.get("token"))
    sid = str(body.get("strategy_id") or "").strip()
    if not sid:
        raise HTTPException(status_code=400, detail="需要 strategy_id")
    from backend.services.allocation.capital_allocator import advance_ladder
    return advance_ladder(sid, positive_4w=bool(body.get("positive_4w", True)))


@router.post("/e5/scan")
def e5_scan(request: Request, body: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    _authorize(request, body.get("token"))
    from backend.services.strategies.event.research_bucket import scan_and_promote
    return scan_and_promote(auto_promote=bool(body.get("auto_promote")))


@router.get("/f4")
def f4_gate(refresh: bool = Query(True)) -> Dict[str, Any]:
    from backend.services.trend_e1_f4_gate import evaluate_f4_gate
    return evaluate_f4_gate(persist=refresh)


@router.get("/paid-data")
def paid_data(refresh: bool = Query(False)) -> Dict[str, Any]:
    from backend.research.paid_data_decision import latest_decision, run_paid_data_decision
    if refresh:
        return run_paid_data_decision(persist=True)
    return latest_decision() or run_paid_data_decision(persist=True)


@router.post("/funding-tail/scan")
def funding_tail_scan(request: Request, body: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    _authorize(request, body.get("token"))
    from backend.services.cashflow.funding_tail_harvest import run_harvest
    return run_harvest(execute=bool(body.get("execute")))


@router.get("/basis/precheck")
def basis_precheck(basis_pct: float = Query(...)) -> Dict[str, Any]:
    from backend.services.arbitrage.basis_entry_gate import basis_entry_allowed
    return basis_entry_allowed(basis_pct=basis_pct)
