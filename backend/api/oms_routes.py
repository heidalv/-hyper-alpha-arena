# -*- coding: utf-8 -*-
"""OMS 接口（/api/oms/*，v3 方向 2，p2-oms-exec）。

只读：
  GET  /api/oms/status              开关 / 统计 / 悬挂单数
  GET  /api/oms/orders              订单列表（?account_id=&status=&limit=）
  GET  /api/oms/orders/{cid}        单笔详情
  GET  /api/oms/reconcile/latest    最近一次对账报告

写（需 X-Risk-Token 或本机）：
  POST /api/oms/reconcile           手动跑一次对账 {"account_id", "exchange", "auto_fix"?}
  POST /api/oms/stuck/scan          手动扫描悬挂单
  POST /api/oms/shadow/execute      影子模式试跑一笔（OMS_SHADOW 强制 true，不发真单）
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, HTTPException, Query, Request

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/oms", tags=["oms"])


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
def oms_status(days: int = Query(1, ge=1, le=30)) -> Dict[str, Any]:
    from backend.services.oms.execution_algo import algo_enabled
    from backend.services.oms.order_store import ensure_schema, shadow_mode, stats, stuck_orders
    from backend.services.oms.reconcile import auto_fix_enabled

    ensure_schema()
    stuck = stuck_orders(older_than_sec=float(os.getenv("OMS_STUCK_SEC", "300") or 300))
    return {
        "exec_algo_enabled": algo_enabled(),
        "shadow": shadow_mode(),
        "auto_fix": auto_fix_enabled(),
        "stats": stats(days),
        "stuck": {"n": len(stuck), "ids": [r["client_order_id"] for r in stuck[:20]]},
        "note": "EXEC_ALGO_ENABLED 与 OMS_SHADOW 都默认关/开（影子），真正接管须显式打开前者并关闭后者",
    }


@router.get("/orders")
def oms_orders(
    account_id: Optional[int] = Query(None),
    exchange: Optional[str] = Query(None),
    symbol: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=1000),
) -> Dict[str, Any]:
    from backend.services.oms.order_store import list_orders

    statuses = [s.strip() for s in status.split(",")] if status else None
    rows = list_orders(account_id=account_id, exchange=exchange, symbol=symbol,
                       statuses=statuses, limit=limit)
    return {"n": len(rows), "orders": rows}


@router.get("/orders/{cid}")
def oms_order_detail(cid: str) -> Dict[str, Any]:
    from backend.services.oms.order_store import get_order

    row = get_order(cid)
    if not row:
        raise HTTPException(status_code=404, detail="订单不存在")
    return row


@router.get("/reconcile/latest")
def oms_reconcile_latest(account_id: Optional[int] = Query(None)) -> Dict[str, Any]:
    from backend.services.oms.reconcile import latest_report

    r = latest_report(account_id)
    if not r:
        return {"ok": False, "reason": "尚无对账报告"}
    return r


@router.post("/reconcile")
def oms_reconcile(request: Request, payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    _authorize(request, payload.get("token"))
    aid = payload.get("account_id")
    ex = payload.get("exchange")
    if not aid or not ex:
        raise HTTPException(status_code=400, detail="需要 account_id 与 exchange")
    from backend.services.oms.jobs import _build_client
    from backend.services.oms.reconcile import reconcile_account_sync

    client = _build_client(str(ex), int(aid))
    if client is None:
        raise HTTPException(status_code=400, detail=f"无法创建 {ex} 客户端")
    return reconcile_account_sync(client, int(aid), str(ex),
                                  auto_fix=payload.get("auto_fix"))


@router.post("/stuck/scan")
def oms_stuck_scan(request: Request, payload: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    _authorize(request, (payload or {}).get("token"))
    from backend.services.oms.jobs import scheduled_stuck_scan

    return scheduled_stuck_scan()


@router.post("/shadow/execute")
def oms_shadow_execute(request: Request, payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """强制影子模式试跑：无论 OMS_SHADOW 当前值，本请求都不发真单。"""
    _authorize(request, payload.get("token"))
    required = ("account_id", "exchange", "symbol", "side", "qty")
    missing = [k for k in required if payload.get(k) is None]
    if missing:
        raise HTTPException(status_code=400, detail=f"缺字段: {missing}")

    import os as _os
    from backend.services.oms.execution_algo import AlgoConfig, AlgoRequest, execute_sync
    from backend.services.oms.jobs import _build_client

    # 临时强制影子
    prev = _os.environ.get("OMS_SHADOW")
    _os.environ["OMS_SHADOW"] = "true"
    try:
        client = _build_client(str(payload["exchange"]), int(payload["account_id"]))
        if client is None:
            raise HTTPException(status_code=400, detail="无法创建客户端（影子模式仍需盘口）")
        req = AlgoRequest(
            account_id=int(payload["account_id"]),
            exchange=str(payload["exchange"]),
            symbol=str(payload["symbol"]),
            side=str(payload["side"]),
            qty=float(payload["qty"]),
            leverage=int(payload.get("leverage") or 1),
            reduce_only=bool(payload.get("reduce_only")),
            strategy_id=payload.get("strategy_id"),
            config=AlgoConfig.from_env(payload.get("config")),
        )
        return execute_sync(client, req).to_dict()
    finally:
        if prev is None:
            _os.environ.pop("OMS_SHADOW", None)
        else:
            _os.environ["OMS_SHADOW"] = prev
