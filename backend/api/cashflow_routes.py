# -*- coding: utf-8 -*-
"""E2 现金流 API（/api/cashflow/*，p1-cashflow）。"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, HTTPException, Query, Request

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/cashflow", tags=["cashflow"])


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
def cashflow_status() -> Dict[str, Any]:
    from backend.services.cashflow.e2a_aster_te import aster_te_status
    from backend.services.cashflow.e2b_carry_sim import carry_sim_latest
    from backend.services.cashflow.carry_small import carry_small_status
    from backend.services.cashflow.idle_earn import idle_earn_status
    from backend.services.rebate_arb.arb_switches import get_arb_switch_status

    snap_path = __import__("pathlib").Path(__file__).resolve().parents[1] / "data" / "cashflow" / "rebate_config_snapshot.json"
    rebate_snap = None
    if snap_path.exists():
        try:
            import json
            rebate_snap = json.loads(snap_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
        "aster_te": aster_te_status(),
        "carry_sim": carry_sim_latest(),
        "carry_small": carry_small_status(),
        "idle_earn": idle_earn_status(),
        "arb_switches": get_arb_switch_status().to_dict(),
        "rebate_snapshot": rebate_snap,
    }


@router.post("/aster-te/refresh")
async def refresh_aster_te(request: Request, body: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    _authorize(request, body.get("token"))
    from backend.services.cashflow.e2a_aster_te import refresh_all_aster_ledgers

    return await refresh_all_aster_ledgers(days=int(body.get("days") or 7))


@router.post("/carry-sim/run")
def run_carry(request: Request, body: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    _authorize(request, body.get("token"))
    from backend.services.cashflow.e2b_carry_sim import run_carry_sim

    return run_carry_sim(
        notional_usd=body.get("notional_usd"),
        min_net_apr=body.get("min_net_apr"),
        horizon_days=body.get("horizon_days"),
        execute_paper=bool(body.get("execute_paper", True)),
    )


@router.post("/idle-earn/tick")
async def idle_tick(request: Request, body: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    _authorize(request, body.get("token"))
    from backend.services.cashflow.idle_earn import idle_earn_tick

    return await idle_earn_tick()


@router.post("/rebate-snapshot")
def rebate_snapshot(request: Request, body: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    _authorize(request, body.get("token"))
    from backend.services.cashflow.rebate_snapshot import snapshot_rebate_config

    return snapshot_rebate_config()
