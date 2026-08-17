"""FreezeCoordinator — 统一冻结入口（2026-08-15 整改）。

设计红线（用户确认）：
  1. 冻结触发只能由本模块一处发出；其余风控机制（PerSymbolRisk / champion /
     circuit_breaker / quick_eval 等）只读本台账，不得各自写冻结状态。
  2. 冻结粒度默认 key 级（交易对），绝不因一个交易对自动冻结整个策略/账户/全局。
  3. 冻结动作链 = 冻结交易对 → 快速因子进化（修复）→ 完成自动解冻；
     修复链默认开启（PB_REPAIR_SPAWN_EVO 缺省 1）。
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_FREEZE_LOCK = threading.Lock()
# 台账：key=(account_id, strategy, symbol) -> {until, why, scope, frozen_at, n}
_FREEZES: Dict[tuple, Dict[str, Any]] = {}
_EVENT_LOG: List[Dict[str, Any]] = []  # 最近 200 条冻结/解冻事件
_EVENT_LOG_MAX = 200


def freeze(
    account_id: int,
    strategy: str,
    symbol: str,
    why: str,
    *,
    cooldown: Optional[float] = None,
) -> Dict[str, Any]:
    """统一冻结入口（仅 key 级）。返回冻结台账条目。

    - 重复冻结：冻结期内幂等（不刷新、不重复计次）。
    - 到期自动解冻由 is_frozen/auto_expire 处理（惰性过期）。
    """
    from backend.services.risk_management.portfolio_budget import portfolio_budget

    sym = str(symbol or "").upper()
    key = (int(account_id or 0), str(strategy or ""), sym)
    now = time.time()
    with _FREEZE_LOCK:
        existing = _FREEZES.get(key)
        if existing and existing["until"] > now:
            logger.info(
                "[FreezeCoordinator] 冻结期内重复触发(幂等忽略) acct=%s %s %s 剩余%ds",
                account_id, strategy, sym, int(existing["until"] - now),
            )
            return existing
        # 交由组合预算执行实际冻结（含冷却衰减与修复链）——这是唯一落点
        portfolio_budget._freeze(
            account_id, strategy, sym, why=why, scope="key", cooldown=cooldown,
        )
        # 台账条目：从 portfolio_budget 读取实际 until
        until = portfolio_budget._key_frozen_until.get(key, now + 900.0)
        entry = {
            "until": until,
            "why": str(why)[:200],
            "scope": "key",
            "frozen_at": now,
            "n": int(portfolio_budget._trigger_count.get(key, 0) or 1),
        }
        _FREEZES[key] = entry
        _push_event("freeze", account_id, strategy, sym, why)
    logger.warning(
        "[FreezeCoordinator] 冻结 %s %s %s (key级): %s", account_id, strategy, sym, why,
    )
    return entry


def unfreeze(account_id: int, strategy: str, symbol: str) -> None:
    """统一解冻入口。"""
    from backend.services.risk_management.portfolio_budget import portfolio_budget

    sym = str(symbol or "").upper()
    key = (int(account_id or 0), str(strategy or ""), sym)
    portfolio_budget.manual_unfreeze(account_id=account_id, strategy=strategy, symbol=sym)
    with _FREEZE_LOCK:
        _FREEZES.pop(key, None)
    _push_event("unfreeze", account_id, strategy, sym, "")
    logger.info("[FreezeCoordinator] 解冻 %s %s %s", account_id, strategy, sym)


def is_frozen(account_id: int, strategy: str, symbol: str) -> bool:
    from backend.services.risk_management.portfolio_budget import portfolio_budget

    sym = str(symbol or "").upper()
    now = time.time()
    key = (int(account_id or 0), str(strategy or ""), sym)
    # 惰性过期：读组合预算的实际状态（权威）
    until = portfolio_budget._key_frozen_until.get(key, 0.0)
    if until > now:
        return True
    # 到期：清台账残留
    with _FREEZE_LOCK:
        _FREEZES.pop(key, None)
    return False


def status() -> Dict[str, Any]:
    """统一冻结台账（供 API/前端）。"""
    from backend.services.risk_management.portfolio_budget import portfolio_budget

    now = time.time()
    active = []
    for key, entry in _FREEZES.items():
        if entry["until"] > now:
            active.append(
                {
                    "account_id": key[0],
                    "strategy": key[1],
                    "symbol": key[2],
                    "remaining_s": max(0, int(entry["until"] - now)),
                    "why": entry["why"],
                    "trigger_n": entry["n"],
                }
            )
    pb = portfolio_budget.status()
    return {
        "active_freeze_count": len(active),
        "active_freeze": sorted(active, key=lambda x: -x["remaining_s"]),
        "recent_events": list(reversed(_EVENT_LOG[-50:])),
        # 组合预算原始状态（全局/账户级仅供监控——设计上不应再有自动触发）
        "budget": {
            "enabled": pb.get("enabled"),
            "global_frozen": pb.get("global_frozen"),
            "account_frozen": pb.get("account_frozen") or {},
            "strategy_frozen": pb.get("strategy_frozen") or {},
            "trigger_count": pb.get("trigger_count") or {},
        },
    }


def register_event(kind: str, account_id: int, strategy: str, symbol: str, why: str) -> None:
    """其余机制（PerSymbolRisk / quick_eval / champion / circuit_breaker）的
    冻结-解冻事件统一登记入口：保证全系统冻结动作在一个台账可查。"""
    _push_event(kind, account_id, strategy, symbol, why)
    logger.info(
        "[FreezeCoordinator] 台账登记 %s acct=%s %s %s: %s",
        kind, account_id, strategy, symbol, str(why)[:120],
    )


def _push_event(kind: str, account_id: int, strategy: str, symbol: str, why: str) -> None:
    with _FREEZE_LOCK:
        _EVENT_LOG.append(
            {
                "ts": time.time(),
                "kind": kind,
                "account_id": account_id,
                "strategy": strategy,
                "symbol": symbol,
                "why": str(why)[:160],
            }
        )
        if len(_EVENT_LOG) > _EVENT_LOG_MAX:
            del _EVENT_LOG[: len(_EVENT_LOG) - _EVENT_LOG_MAX]


# 单例
freeze_coordinator = freeze  # 模块级函数即入口；保留实例式别名便于未来扩展
