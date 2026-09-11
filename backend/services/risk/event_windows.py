# -*- coding: utf-8 -*-
"""事件避险窗口（RiskEngine 检查 4b，读 market_events 事件总线）。

Phase 0 只用**无需统计证据也成立**的确定性规则（其余角色——开仓信号、持仓调节——按方案第四节
必须先过 event_study 显著性门，Phase 1/2 再接）：

  announcement.delisting        该币 72h 内禁止任何新开仓（下架币流动性/价格失序）
  announcement.monitoring_tag   该币 48h 内禁止新开多（监控标签 = 退市预警）
  liquidation.market_cascade    severity ≥ 5 → 全市场窗口内禁止新开仓（黑天鹅级）
  liquidation.cascade           severity ≥ 4 → 该币窗口内禁止**顺着被清算方向**新开
                                （多头级联 direction<0 → 禁开多；空头级联 → 禁开空）
  news.high_impact              severity ≥ 4（强度 ≥ 9）且方向为负 → 禁开多；
                                默认关闭（RISK_EVENT_NEWS_BLOCK=false），待 E5-5 影子验证后打开

缓存 30s（每笔订单不打库）；读取失败 → 不阻断（fail-open：事件层缺数据不能拖停交易，
但会记录 error 供 status 展示）。总开关 RISK_EVENT_WINDOWS_ENABLED（默认 true）。
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_CACHE: Dict[str, Any] = {"ts": 0.0, "rows": [], "error": None}
_CACHE_LOCK = threading.Lock()
CACHE_TTL_SEC = 30.0


def _env_true(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def _side_is_long(side: Optional[str]) -> Optional[bool]:
    s = (side or "").strip().lower()
    if s in ("buy", "long", "open_long"):
        return True
    if s in ("sell", "short", "open_short"):
        return False
    return None


def event_block_reason(events: List[Dict[str, Any]], symbol: Optional[str], side: Optional[str], *,
                       now_ms: Optional[int] = None, news_block: Optional[bool] = None) -> Optional[Dict[str, Any]]:
    """纯函数：给定"仍在窗口内"的事件列表（market_events_store.active_risk_windows 的输出）、
    目标币与方向，返回首个命中的阻断原因，或 None。"""
    now = now_ms or int(time.time() * 1000)
    sym = (symbol or "").upper().replace("USDT", "").replace("/", "").replace("-PERP", "") or None
    is_long = _side_is_long(side)
    allow_news = news_block if news_block is not None else _env_true("RISK_EVENT_NEWS_BLOCK", False)
    for ev in events or []:
        try:
            if int(ev.get("window_ends_ms") or 0) < now:
                continue
            et = str(ev.get("event_type") or "")
            ev_sym = (ev.get("symbol") or None)
            same_symbol = (ev_sym is None) or (sym is not None and str(ev_sym).upper() == sym)
            sev = int(ev.get("severity") or 0)
            direction = ev.get("direction")
            direction = float(direction) if direction is not None else 0.0
            hit = None
            if et == "announcement.delisting" and ev_sym is not None and same_symbol:
                hit = "delisting"
            elif et == "announcement.monitoring_tag" and ev_sym is not None and same_symbol and is_long is not False:
                hit = "monitoring_tag_no_long"
            elif et == "liquidation.market_cascade" and sev >= 5:
                hit = "market_cascade"
            elif et == "liquidation.cascade" and sev >= 4 and ev_sym is not None and same_symbol:
                # direction<0 = 多头被清算为主 → 禁开多；>0 → 禁开空；方向未知 → 都禁
                if is_long is None or (direction < 0 and is_long) or (direction > 0 and not is_long):
                    hit = "cascade_same_side"
            elif et == "news.high_impact" and allow_news and sev >= 4 and direction < 0 and same_symbol and is_long is not False:
                hit = "negative_news_no_long"
            if hit:
                return {
                    "rule": hit, "event_type": et, "event_id": ev.get("id"), "symbol": ev_sym, "severity": sev,
                    "direction": direction, "title": (ev.get("title") or "")[:160],
                    "window_ends_ms": int(ev.get("window_ends_ms") or 0), "event_ts_ms": int(ev.get("ts_ms") or 0),
                }
        except Exception:  # 单条事件字段异常不影响其余判断
            continue
    return None


def _load_active(force: bool = False) -> List[Dict[str, Any]]:
    now = time.time()
    with _CACHE_LOCK:
        if not force and now - float(_CACHE["ts"]) < CACHE_TTL_SEC:
            return list(_CACHE["rows"])
    rows: List[Dict[str, Any]] = []
    err: Optional[str] = None
    try:
        from backend.services.events.market_events_store import active_risk_windows
        rows = active_risk_windows(None, lookback_hours=96.0, min_severity=3)
    except Exception as exc:
        err = f"{type(exc).__name__}: {str(exc)[:160]}"
        logger.debug("[event_windows] 读取事件失败: %s", err)
    with _CACHE_LOCK:
        _CACHE["ts"] = now
        _CACHE["rows"] = list(rows)
        _CACHE["error"] = err
    return rows


def check(symbol: Optional[str], side: Optional[str]) -> Optional[Dict[str, Any]]:
    """RiskEngine 调用：命中返回阻断信息，否则 None。总开关关闭 → None。"""
    if not _env_true("RISK_EVENT_WINDOWS_ENABLED", True):
        return None
    return event_block_reason(_load_active(), symbol, side)


def status() -> Dict[str, Any]:
    rows = _load_active()
    return {
        "enabled": _env_true("RISK_EVENT_WINDOWS_ENABLED", True),
        "news_block": _env_true("RISK_EVENT_NEWS_BLOCK", False),
        "active_events": len(rows),
        "blocking_candidates": [
            {"event_type": r.get("event_type"), "symbol": r.get("symbol"), "severity": r.get("severity"),
             "window_ends_ms": r.get("window_ends_ms"), "title": (r.get("title") or "")[:100]}
            for r in rows if r.get("event_type") in (
                "announcement.delisting", "announcement.monitoring_tag", "liquidation.market_cascade",
                "liquidation.cascade", "news.high_impact")
        ][:50],
        "cache_age_sec": round(time.time() - float(_CACHE["ts"]), 1) if _CACHE["ts"] else None,
        "error": _CACHE.get("error"),
    }
