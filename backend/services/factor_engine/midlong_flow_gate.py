"""midlong_flow_gate — 中线因子路由资金流一致性门（U2-1，2026-08-25）。

依据：统一计划 U2-1 / 混合决策 L0「mid 只吃 4h/1d OHLCV」缺口（复查确认仍在）。
语义（fail-open，证据不齐绝不拦截）：
  - CVD 与 Taker 比同时逆着因子方向 → hold（防止在资金流背离时接刀）；
  - 数据来源：market_summary[sym] 内嵌 flow 字段优先；否则 TTL 60s 缓存
    回退 capture_flow_indicators_for_symbol（与 KlineAnalyst 同源）；
  - 开关：FACTOR_ROUTE_FLOW_GATE=false 完全停用。
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

_TTL = 60.0
_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}
_lock = threading.Lock()


def _enabled() -> bool:
    return os.getenv("FACTOR_ROUTE_FLOW_GATE", "true").strip().lower() in ("1", "true", "yes", "on")


def _flow_from_summary(symbol: str, market_summary: Optional[dict]) -> Optional[Dict[str, Any]]:
    try:
        ms = (market_summary or {}).get(symbol) or {}
        if not isinstance(ms, dict):
            return None
        for k in ("flow", "orderflow", "flow_indicators"):
            v = ms.get(k)
            if isinstance(v, dict) and v:
                return v
        # 兼容平铺字段
        if ms.get("cvd_delta_1h") is not None and ms.get("taker_ratio_1h") is not None:
            return ms
    except Exception:
        pass
    return None


def _flow_from_db(symbol: str) -> Optional[Dict[str, Any]]:
    now = time.time()
    with _lock:
        hit = _cache.get(symbol)
        if hit and now - hit[0] < _TTL:
            return hit[1]
    try:
        from backend.database.connection import SessionLocal
        from backend.services.kline_enrichment_service import capture_flow_indicators_for_symbol
        with SessionLocal() as db:
            flow = capture_flow_indicators_for_symbol(db, symbol)
        if isinstance(flow, dict) and flow.get("flow_data_ok"):
            with _lock:
                _cache[symbol] = (now, flow)
            return flow
        with _lock:
            _cache[symbol] = (now, {})   # 缓存负结果，避免每轮查库
    except Exception as e:
        logger.info("[MidFlowGate] %s flow 获取失败(fail-open): %s", symbol, e)
    return None


def mid_flow_consistency_gate(
    symbol: str,
    action: str,
    market_summary: Optional[dict] = None,
) -> Tuple[bool, str]:
    """返回 (放行, 原因)。证据不齐/异常 → (True, 'no_evidence') fail-open。"""
    if not _enabled():
        return True, "disabled"
    sym = str(symbol or "").upper()
    action = str(action or "").lower()
    if action not in ("buy", "sell"):
        return True, "no_action"
    flow = _flow_from_summary(sym, market_summary) or _flow_from_db(sym)
    if not flow:
        return True, "no_evidence"

    try:
        cvd_delta = float(flow.get("cvd_delta_1h") or 0.0)
        taker = float(flow.get("taker_ratio_1h") or 1.0)
    except (TypeError, ValueError):
        return True, "bad_flow_fields"

    # 双向背离才拦（单信号不放行=保守；单信号反向也不拦=避免噪声误杀）
    if action == "buy" and cvd_delta < 0 and taker < 0.8:
        return False, f"cvd_delta={cvd_delta:,.0f}<0 且 taker_ratio={taker:.2f}<0.8 逆多头"
    if action == "sell" and cvd_delta > 0 and taker > 1.2:
        return False, f"cvd_delta={cvd_delta:,.0f}>0 且 taker_ratio={taker:.2f}>1.2 逆空头"
    return True, "flow_consistent"
