"""编排器后台缓存合并 — 从 monolith 迁出。"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class OrchCacheContext:
    market_scan_cache: Dict[str, Any]
    market_scan_cache_ts: float = 0.0
    orch_bg_thread: Any = None
    last_orch_decisions: Optional[dict] = None
    last_orch_decisions_ts: float = 0.0


def orch_bg_cache_covers_symbols(symbols: list, ctx: OrchCacheContext) -> bool:
    """OrchBG 缓存是否覆盖本 tick 全部币种且未过期。"""
    try:
        from backend.config.settings import (
            FULLAUTO_ORCH_SKIP_SYNC_WHEN_CACHE_FRESH,
            ORCHBG_CACHE_FRESH_SEC,
        )

        if not FULLAUTO_ORCH_SKIP_SYNC_WHEN_CACHE_FRESH:
            return False
        if not ctx.orch_bg_thread or not ctx.orch_bg_thread.is_alive():
            return False
        cache_ts = float(ctx.market_scan_cache_ts or 0)
        if cache_ts <= 0 or (time.time() - cache_ts) > float(ORCHBG_CACHE_FRESH_SEC):
            return False
        for raw in symbols or []:
            sym = str(raw or "").strip().upper()
            if not sym:
                continue
            cached = ctx.market_scan_cache.get(sym) or ctx.market_scan_cache.get(raw)
            if not isinstance(cached, dict):
                return False
            orch = cached.get("orchestrator")
            if not isinstance(orch, dict) or not orch:
                return False
        return bool(symbols)
    except Exception:
        return False


def merge_orch_from_scan_cache(market_summary: dict, symbols: list, ctx: OrchCacheContext) -> None:
    """将 OrchBG 写入 _market_scan_cache 的编排器结果合并进 market_summary。"""
    for raw in symbols or []:
        sym = str(raw or "").strip().upper()
        if not sym:
            continue
        cached = ctx.market_scan_cache.get(sym) or ctx.market_scan_cache.get(raw)
        if not isinstance(cached, dict):
            continue
        orch = cached.get("orchestrator")
        if isinstance(orch, dict) and orch:
            market_summary.setdefault(sym, {})
            market_summary[sym]["orchestrator"] = orch
        nat = cached.get("recommended_nature")
        if nat:
            market_summary.setdefault(sym, {})
            market_summary[sym]["recommended_nature"] = nat


def ensure_fresh_orch_decisions(market_summary: dict, ctx: OrchCacheContext) -> dict:
    """_last_orch_decisions 超过阈值时轻量 refresh（限 6 币）。"""
    import logging

    from backend.config.settings import MIDLONG_ORCH_STALE_REFRESH_SEC

    logger = logging.getLogger(__name__)
    _orch_decs = ctx.last_orch_decisions or {}
    _ts = float(ctx.last_orch_decisions_ts or 0)
    if _orch_decs and (time.time() - _ts) < MIDLONG_ORCH_STALE_REFRESH_SEC:
        return _orch_decs
    try:
        from backend.services.multi_timeframe_orchestrator import mt_orchestrator as _mto

        _symbols = list((market_summary or {}).keys())[:6]
        if not _symbols:
            return _orch_decs
        _orch_decs = _mto.evaluate_portfolio(_symbols)
        ctx.last_orch_decisions = _orch_decs
        ctx.last_orch_decisions_ts = time.time()
        logger.info("[OrchSnapshot] 轻量 refresh: %s symbols", len(_orch_decs))
    except Exception as _err:
        logger.debug("[OrchSnapshot] refresh 失败: %s", _err)
    return _orch_decs
