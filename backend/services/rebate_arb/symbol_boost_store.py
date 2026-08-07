"""
Symbol Boost 运行时存储 — S8 币种加成动态刷新

官方 symbol boost 每期会变，旧版写死在 YAML/代码里会过期。
本模块提供运行时可刷新的 boost map：
- 规则同步任务（rule_sync_scheduler）定期调用 refresh_from_exchange()
- 也可通过 update_symbol_boost_map() 手动/API 更新
- S8 的 symbol_boost() 优先读这里，过期或为空时回退实例自带 map
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# 运行时 boost 超过该时长未刷新则视为过期（回退静态 map）
DEFAULT_TTL_SECONDS = 24 * 3600

_lock = threading.Lock()
_state: Dict[str, Any] = {
    "map": {},          # {"BTC/USDT": 1.5, ...}
    "updated_at": 0.0,
    "source": "",
}


def get_runtime_symbol_boost_map(max_age: float = DEFAULT_TTL_SECONDS) -> Optional[Dict[str, float]]:
    """返回未过期的运行时 boost map；为空或过期时返回 None。"""
    with _lock:
        mapping = _state.get("map") or {}
        updated_at = float(_state.get("updated_at") or 0)
    if not mapping:
        return None
    if max_age > 0 and (time.time() - updated_at) > max_age:
        return None
    return dict(mapping)


def get_symbol_boost_status() -> Dict[str, Any]:
    """boost 状态（健康检查/前端展示用）。"""
    with _lock:
        return {
            "map": dict(_state.get("map") or {}),
            "updated_at": float(_state.get("updated_at") or 0),
            "source": _state.get("source") or "",
            "stale": (
                not _state.get("map")
                or (time.time() - float(_state.get("updated_at") or 0)) > DEFAULT_TTL_SECONDS
            ),
        }


def update_symbol_boost_map(mapping: Dict[str, float], source: str = "manual") -> bool:
    """写入新的 boost map（key 统一大写交易对）。"""
    if not isinstance(mapping, dict) or not mapping:
        return False
    try:
        normalized = {
            str(k).upper(): float(v)
            for k, v in mapping.items()
            if v is not None and float(v) > 0
        }
    except (TypeError, ValueError) as exc:
        logger.warning("[SymbolBoostStore] 非法 boost map: %s", exc)
        return False
    if not normalized:
        return False
    with _lock:
        _state["map"] = normalized
        _state["updated_at"] = time.time()
        _state["source"] = source
    logger.info(
        "[SymbolBoostStore] boost map 已更新 (%d symbols, source=%s)",
        len(normalized), source,
    )
    return True


def refresh_from_exchange() -> bool:
    """
    从交易所适配器拉取最新 symbol boost（best-effort）。

    适配器需实现 get_symbol_boosts() -> {"BTC/USDT": 1.5, ...}；
    未实现或拉取失败时保持现状（不清空旧数据）。
    """
    try:
        from backend.services.exchange.exchange_manager import get_exchange_manager
        from backend.services.arbitrage.async_bridge import run_async_safe

        mgr = get_exchange_manager()
        client = mgr.get_client("asterdex") if mgr else None
        if client is None or not hasattr(client, "get_symbol_boosts"):
            logger.debug("[SymbolBoostStore] adapter 未实现 get_symbol_boosts，跳过刷新")
            return False

        boosts = run_async_safe(client.get_symbol_boosts(), default=None)
        if isinstance(boosts, dict) and boosts:
            return update_symbol_boost_map(boosts, source="asterdex_adapter")
        return False
    except Exception as exc:
        logger.debug("[SymbolBoostStore] 刷新失败（保留旧数据）: %s", exc)
        return False
