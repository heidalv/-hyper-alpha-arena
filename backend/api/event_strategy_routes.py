# -*- coding: utf-8 -*-
"""E5 事件影子策略接口（/api/strategies/event/*，v3 方向 4，p2-event-strategies）。

只读：
  GET  /api/strategies/event/list                    三个策略的开关与影子 KPI 概览
  GET  /api/strategies/event/status/{strategy_id}    单策略详情（KPI + 晋升门 + 策略自带上下文）
  GET  /api/strategies/event/kpi                     三策略 KPI 汇总（同 e5_shadow_kpi 任务产物）
  GET  /api/strategies/event/detect/{strategy_id}    近窗干跑检测（不入账，用于人工核对规则）
  GET  /api/strategies/event/hedge_window            E5-5 当前避险窗口（影子期只展示不生效）

写（需 X-Risk-Token 或本机）：
  POST /api/strategies/event/shadow                  立即跑一次影子扫描（入账，幂等）
  POST /api/strategies/event/backtest                历史回测（重，默认 180 天；不入账）

三个策略的 `live()` 一律抛 NotImplementedError，未过影子门前没有任何下单路径。
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, HTTPException, Query, Request

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/strategies/event", tags=["strategies-event"])


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


def _get(strategy_id: str):
    from backend.services.strategies.event import get_strategy

    strat = get_strategy(strategy_id)
    if strat is None:
        from backend.services.strategies.event import registered_strategies

        raise HTTPException(status_code=404, detail=f"未知策略 {strategy_id}，可用: {registered_strategies()}")
    return strat


@router.get("/list")
def list_strategies(days: int = Query(90, ge=1, le=365)) -> Dict[str, Any]:
    from backend.services.strategies.event import registered_strategies
    from backend.services.strategies.event.base import COST_BP, PROMOTION_MIN_N, env_true

    items: List[Dict[str, Any]] = []
    for sid in registered_strategies():
        try:
            strat = _get(sid)
            k = strat.kpi(days=days)
            items.append({
                "strategy": sid, "description": strat.description,
                "shadow_enabled": strat.shadow_enabled(),
                "default_horizon_h": strat.default_horizon_h,
                "n_scored": k.get("n_scored"), "n_open": k.get("n_open"),
                "mean_excess_bp": k.get("mean_excess_bp"), "hit_rate": k.get("hit_rate"),
                "net_lower_bp": k.get("net_lower_bp"),
                "promotion_ready": k.get("promotion_ready"),
                "promotion_reason": k.get("promotion_reason"),
            })
        except Exception as exc:
            items.append({"strategy": sid, "error": str(exc)[:200]})
    return {
        "lane": "shadow", "global_enabled": env_true("E5_SHADOW_ENABLED", True),
        "promotion_gate": {"min_n": PROMOTION_MIN_N, "cost_bp": COST_BP,
                           "rule": "N ≥ 30 且平均超额 95% 下界 > 14bp 往返成本"},
        "strategies": items,
    }


@router.get("/status/{strategy_id}")
def strategy_status(strategy_id: str) -> Dict[str, Any]:
    return _get(strategy_id).status()


@router.get("/kpi")
def kpi(days: int = Query(90, ge=1, le=365)) -> Dict[str, Any]:
    from backend.services.strategies.event import registered_strategies

    out: Dict[str, Any] = {"days": days, "strategies": {}, "promotion_ready": []}
    for sid in registered_strategies():
        try:
            k = _get(sid).kpi(days=days)
            out["strategies"][sid] = k
            if k.get("promotion_ready"):
                out["promotion_ready"].append(sid)
        except Exception as exc:
            out["strategies"][sid] = {"error": str(exc)[:200]}
    return out


@router.get("/detect/{strategy_id}")
def detect(strategy_id: str, lookback_h: float = Query(24.0, ge=0.5, le=24 * 90),
           limit: int = Query(100, ge=1, le=1000)) -> Dict[str, Any]:
    """干跑检测：只返回候选信号，不写 signal_ledger。"""
    return _get(strategy_id).shadow(lookback_h=lookback_h, dry_run=True, limit=limit)


@router.get("/hedge_window")
def hedge_window() -> Dict[str, Any]:
    return _get("e5_5_news_hedge").hedge_window()


@router.post("/shadow")
def run_shadow(request: Request, payload: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    _authorize(request, payload.get("token"))
    sid = str(payload.get("strategy") or "all")
    if sid == "all":
        from backend.services.strategies.event.jobs import run_shadow_scan

        return run_shadow_scan()
    return _get(sid).shadow(
        lookback_h=payload.get("lookback_h"),
        dry_run=bool(payload.get("dry_run", False)),
    )


@router.post("/backtest")
def run_backtest(request: Request, payload: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    """历史回测（重，同步执行；建议 days ≤ 365）。不入账、不改状态。"""
    _authorize(request, payload.get("token"))
    sid = str(payload.get("strategy") or "")
    if not sid:
        raise HTTPException(status_code=400, detail="缺少 strategy")
    days = int(payload.get("days") or 180)
    horizons = payload.get("horizons_h") or (1, 2, 4, 8, 24)
    return _get(sid).backtest(days=days, horizons_h=tuple(int(h) for h in horizons))
