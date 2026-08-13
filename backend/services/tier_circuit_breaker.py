"""
P0-E 分层熔断 — 三周期隔离（设计: docs/中长线改造升级设计_2026-08-14.md §4.1）

核心铁律：
  - 周期级：每个 tier（short/mid/long）有**独立的**日亏预算；
    当日该 tier 已实现亏损超过预算 → 只冻结该 tier 的新开仓。
  - **绝不**因单币/单周期亏损冻结其他周期（无账户级统一冻结）。
  - 冻结只挡「新开仓」；已持仓位的管理、平仓、减仓一律不受影响。
  - 币种级熔断由 symbol_risk.py 层负责（本模块不重复实现）。

数据源：paper_orders 当日已实现盈亏（status=filled，剔除清理单），
tier 归因优先级：trade_nature(NATURE_TO_TIER) > AIStrategy.timeframe_tier
（后者有 server_default='mid'，会把 legacy 短线单误归入中线）。
无法归因的订单计入 unknown 桶，不触发任何冻结（fail-open，避免误伤）。

配置（env，权益百分比；0 = 禁用该周期熔断）：
  TIER_DAILY_LOSS_BUDGET_PCT_SHORT  默认 2.0
  TIER_DAILY_LOSS_BUDGET_PCT_MID    默认 2.0
  TIER_DAILY_LOSS_BUDGET_PCT_LONG   默认 3.0

状态为进程内内存（按日期自动重置，跨日清零）；只读快照供透明化接口。
"""
from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime
from typing import Dict, Optional, Tuple

from sqlalchemy import text as sa_text

logger = logging.getLogger(__name__)

_state_lock = threading.Lock()
# key: f"{account_id}_{tier}" -> {
#   "day": str, "loss": float, "budget": float, "budget_pct": float,
#   "frozen": bool, "reason": str, "frozen_at": float,
# }
_tier_state: Dict[str, dict] = {}

_TIERS = ("short", "mid", "long")
_DEFAULT_BUDGET_PCT = {"short": 2.0, "mid": 2.0, "long": 3.0}
_BUDGET_MIN_PCT = 0.0     # 0 = 禁用
_BUDGET_MAX_PCT = 20.0


def _budget_pct(tier: str) -> float:
    """该 tier 的日亏预算（权益百分比）。异常/越界时回退默认值。"""
    try:
        raw = os.getenv(f"TIER_DAILY_LOSS_BUDGET_PCT_{tier.upper()}", "")
        pct = float(raw) if str(raw).strip() else _DEFAULT_BUDGET_PCT.get(tier, 2.0)
        if not (0 <= pct <= _BUDGET_MAX_PCT):
            return _DEFAULT_BUDGET_PCT.get(tier, 2.0)
        return pct
    except Exception:
        return _DEFAULT_BUDGET_PCT.get(tier, 2.0)


def _state_key(account_id: int, tier: str) -> str:
    _t = (tier or "").strip().lower()
    if _t not in _TIERS:
        _t = "mid"
    return f"{int(account_id)}_{_t}"


def _today_key() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def compute_tier_daily_pnl(db, account_id: int) -> Dict[str, float]:
    """当日各 tier 已实现盈亏（paper_orders 权威账本）。异常降级为空。"""
    out = {"short": 0.0, "mid": 0.0, "long": 0.0, "unknown": 0.0}
    try:
        today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        rows = db.execute(sa_text("""
            SELECT
              COALESCE(
                CASE o.trade_nature
                  WHEN 'scalp' THEN 'short'
                  WHEN 'intraday' THEN 'short'
                  WHEN 'swing' THEN 'mid'
                  WHEN 'trend_follow' THEN 'long'
                  WHEN 'position' THEN 'long'
                  ELSE NULL
                END,
                s.timeframe_tier,
                'unknown'
              ) AS tier,
              SUM(COALESCE(o.pnl, 0)) AS total_pnl
            FROM paper_orders o
            LEFT JOIN ai_strategies s ON s.strategy_id = o.strategy_id
            WHERE o.account_id = :acct
              AND o.status = 'filled'
              AND o.created_at >= :today_start
              AND COALESCE(o.close_reason, '') NOT IN ('old_position_cleanup', 'smoke_test_cleanup')
            GROUP BY 1
        """), {"acct": int(account_id), "today_start": today_start}).fetchall()
        for row in rows:
            _t = str(row[0] or "unknown").strip().lower()
            if _t in out:
                out[_t] += float(row[1] or 0)
    except Exception as e:
        logger.debug("[TierCircuit] compute_tier_daily_pnl skip: %s", e)
    return out


def check_and_update(db, account_id: int, equity: Optional[float] = None) -> Dict[str, dict]:
    """每日巡检：计算各 tier 当日亏损，超预算则冻结该 tier（只影响新开仓）。

    返回快照 {tier: state}。跨日自动解冻（daily budget 语义）。
    同一 tier 当日一旦冻结，当日不再自动解冻（除非预算外亏损恢复——保守不回滚）。
    """
    today = _today_key()
    pnl_map = compute_tier_daily_pnl(db, account_id)

    if equity is None or equity <= 0:
        try:
            from backend.services.paper_trading_engine import paper_engine
            bal = paper_engine.get_balance(db, int(account_id)) or {}
            equity = float(bal.get("total_equity", 0))
        except Exception:
            pass
    if equity is None or equity <= 0:
        equity = 10000.0

    snapshot: Dict[str, dict] = {}
    with _state_lock:
        for tier in _TIERS:
            key = _state_key(account_id, tier)
            prev = _tier_state.get(key)
            # 跨日自动重置
            if prev and prev.get("day") != today:
                prev = None
            budget_pct = _budget_pct(tier)
            loss = float(pnl_map.get(tier, 0.0))
            budget = round(equity * budget_pct / 100.0, 2)
            frozen = bool(prev and prev.get("frozen"))
            reason = (prev or {}).get("reason", "")
            frozen_at = (prev or {}).get("frozen_at")

            if not frozen and budget_pct > 0 and loss < 0 and abs(loss) >= budget:
                frozen = True
                frozen_at = time.time()
                reason = (
                    f"{tier}层当日亏损 ${abs(loss):.2f} "
                    f"({abs(loss) / equity * 100:.1f}% 权益) 达到日亏预算 "
                    f"${budget:.2f} ({budget_pct:.1f}%) — 仅冻结本周期新开仓，"
                    f"其他周期不受影响"
                )
                logger.warning("[TierCircuit] FREEZE account=%s tier=%s %s", account_id, tier, reason)
            elif frozen and (prev or {}).get("reason"):
                # 已冻结：保留原始原因（含冻结时刻）
                pass

            state = {
                "tier": tier,
                "day": today,
                "loss": round(loss, 2),
                "budget": budget,
                "budget_pct": budget_pct,
                "equity": round(float(equity), 2),
                "frozen": frozen,
                "reason": reason,
                "frozen_at": frozen_at,
            }
            _tier_state[key] = state
            snapshot[tier] = dict(state)
    return snapshot


def is_tier_open_blocked(account_id: int, tier: str) -> Tuple[bool, str]:
    """开仓前调用（只读内存态）。冻结只挡新开仓。"""
    key = _state_key(account_id, tier)
    with _state_lock:
        state = _tier_state.get(key)
        if not state:
            return False, ""
        if state.get("day") != _today_key():
            return False, ""
        if state.get("frozen"):
            return True, str(state.get("reason") or f"{tier} 层日亏预算熔断中")
    return False, ""


def get_tier_circuit_snapshot(account_id: int) -> Dict[str, dict]:
    """只读快照（透明化接口数据源）。"""
    today = _today_key()
    with _state_lock:
        out = {}
        for tier in _TIERS:
            key = _state_key(account_id, tier)
            state = _tier_state.get(key)
            if not state or state.get("day") != today:
                budget_pct = _budget_pct(tier)
                out[tier] = {
                    "tier": tier, "day": today, "loss": None, "budget": None,
                    "budget_pct": budget_pct, "equity": None,
                    "frozen": False, "reason": "", "frozen_at": None,
                }
                continue
            out[tier] = dict(state)
    return out


def reset_state(account_id: Optional[int] = None) -> int:
    """测试/手动重置用。account_id=None 清空全部。"""
    with _state_lock:
        if account_id is None:
            n = len(_tier_state)
            _tier_state.clear()
            return n
        keys = [k for k in _tier_state if k.startswith(f"{int(account_id)}_")]
        for k in keys:
            del _tier_state[k]
        return len(keys)
