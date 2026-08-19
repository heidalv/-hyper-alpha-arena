"""symbol_penalty — 亏损币状态机（设计 D2，2026-08-19）。

由日报亏损归因驱动：
- 连续 2 天净亏 且 累计>=5 笔 → penalty=0.5（信号强度减半）
- 连续 5 天仍亏 → watchlisted（禁开该币，待复审）
- 连续 3 天转盈 → 自动恢复（penalty=1.0，解除观察）
状态落盘 data/symbol_penalty_state.json，全部动作可观测、可审计。
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_STATE_PATH = os.path.join("data", "symbol_penalty_state.json")
_PENALTY = 0.5
_LOSS_DAYS_FOR_PENALTY = 2
_LOSS_DAYS_FOR_WATCH = 5
_WIN_DAYS_FOR_RECOVER = 3
_MIN_TRADES = 5


def _load() -> Dict[str, Any]:
    try:
        if os.path.exists(_STATE_PATH):
            with open(_STATE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {"symbols": {}, "updated_at": None}


def _save(state: Dict[str, Any]) -> None:
    try:
        os.makedirs(os.path.dirname(os.path.abspath(_STATE_PATH)), exist_ok=True)
        tmp = _STATE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        os.replace(tmp, _STATE_PATH)
    except Exception as e:
        logger.warning("[SymbolPenalty] 状态落盘失败: %s", e)


def update_daily(symbol: str, pnl: float, n_trades: int, date: str) -> Dict[str, Any]:
    """日报驱动：输入某币当日归因（pnl/n_trades），推进状态机，返回该币当前状态。

    幂等：同日重复调用（日报重复生成/多周期同币）直接返回当前状态，不重复推进。
    亏损/盈利日判定：当日有平仓(n>=1) 且 累计平仓笔数 >= _MIN_TRADES。
    """
    state = _load()
    syms = state.setdefault("symbols", {})
    s = syms.setdefault(symbol.upper(), {
        "penalty": 1.0, "watchlisted": False,
        "consecutive_loss_days": 0, "consecutive_win_days": 0,
        "total_trades": 0, "last_date": None, "history": [],
    })
    if s.get("last_date") == date:
        return dict(s)
    _n = max(int(n_trades), 0)
    s["total_trades"] += _n
    s["last_date"] = date
    if _n >= 1 and int(s["total_trades"]) >= _MIN_TRADES:
        if pnl < 0:
            s["consecutive_loss_days"] += 1
            s["consecutive_win_days"] = 0
        elif pnl >= 0:
            s["consecutive_win_days"] += 1
            s["consecutive_loss_days"] = 0
    # 状态机推进
    if s["consecutive_loss_days"] >= _LOSS_DAYS_FOR_WATCH:
        s["watchlisted"] = True
        s["penalty"] = 0.0
    elif s["consecutive_loss_days"] >= _LOSS_DAYS_FOR_PENALTY:
        s["penalty"] = _PENALTY
    if s["consecutive_win_days"] >= _WIN_DAYS_FOR_RECOVER:
        s["watchlisted"] = False
        s["penalty"] = 1.0
        s["total_trades"] = 0  # 恢复后重置累计，全新观察期
    s["history"].append({"date": date, "pnl": round(pnl, 4), "n": _n,
                         "penalty": s["penalty"], "watchlisted": s["watchlisted"]})
    s["history"] = s["history"][-60:]
    state["updated_at"] = date
    _save(state)
    return dict(s)


def get_penalty(symbol: str) -> float:
    """当前币的惩罚系数（1.0=正常，0.5=减半，0.0=watchlisted 禁开）。"""
    state = _load()
    s = state.get("symbols", {}).get(symbol.upper())
    return float(s.get("penalty", 1.0)) if s else 1.0


def is_watchlisted(symbol: str) -> bool:
    state = _load()
    s = state.get("symbols", {}).get(symbol.upper())
    return bool(s.get("watchlisted")) if s else False


def snapshot() -> Dict[str, Any]:
    return _load()
