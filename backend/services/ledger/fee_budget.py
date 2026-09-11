# -*- coding: utf-8 -*-
"""账户级日手续费预算门（v3 F2）。

为什么需要它
------------
账户 14 的实测：权益 500 → 203，其中手续费 111（占亏损 37%），短线车道
08-26→09-02 净 -$1,193 而费用就有 $1,420 —— 费用不是"成本项"，是这套系统
的第一亏损来源。费用必须像风险预算一样被硬约束，而不是靠"少交易"的自觉。

口径
----
    当日已付手续费（paper_orders.fee，status=filled，本地日界）
  + 本单预估手续费（名义 × taker 费率）
  ≤ 权益 × FEE_BUDGET_DAILY_PCT（默认 0.3%）

- 超预算 → 只拦"新开 / 加仓"，永远不拦平仓 / 减仓（风险优先于成本）。
- 不受锁强度配置（disable_loss_locks）影响：这是账户级硬预算，不是"亏损锁"。
- 查询结果按账户缓存 10 秒：短线循环每 tick 会为几十个币各调一次。
- 任何异常 fail-open 并记 warning：预算门不应因数据库抖动阻断主流程，
  但必须留下痕迹供 ops 页面发现。

live 账户
---------
live 成交费用来自交易所 income/userTrades（live_income_ledger），本模块提供
`fees_today` 注入口，由 trading_commands 侧传入；拿不到时按 0 处理并标注。
"""
from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_CACHE_TTL_S = 10.0
_cache_lock = threading.Lock()
# account_id -> (expires_at, fees_today)
_fees_cache: Dict[int, tuple] = {}
# 最近一次拦截/异常计数（ops 观测用）
_stats: Dict[str, Any] = {"blocked": 0, "allowed": 0, "errors": 0, "last_block": None}


@dataclass
class FeeBudgetVerdict:
    allowed: bool
    reason: str
    fees_today: float
    est_fee: float
    budget: float
    equity: float
    ratio: float          # (fees_today + est_fee) / equity
    budget_pct: float     # 预算比例（权益的百分比，如 0.3）
    source: str = "paper"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def daily_fee_budget_pct() -> float:
    """日手续费预算（权益百分比）。FEE_BUDGET_DAILY_PCT 默认 0.3；≤0 表示关闭。"""
    try:
        return float(os.getenv("FEE_BUDGET_DAILY_PCT", "0.3") or 0.3)
    except ValueError:
        return 0.3


def default_taker_rate() -> float:
    """预估费率：优先与模拟引擎同源（backtest_engine.TAKER_FEE），失败退 3.5bp。"""
    try:
        from backend.services.backtest_engine.backtest_engine import TAKER_FEE
        return float(TAKER_FEE)
    except Exception:
        return 0.00035


def estimate_fee(notional: float, rate: Optional[float] = None) -> float:
    """单边预估手续费。"""
    n = float(notional or 0)
    if n <= 0:
        return 0.0
    return n * float(rate if rate is not None else default_taker_rate())


def _local_day_start() -> datetime:
    # 与 tier_circuit_breaker.compute_tier_daily_pnl 一致：本地日界，paper_orders.created_at 为 naive 本地时间
    return datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)


def fees_paid_today_paper(db, account_id: int, *, use_cache: bool = True) -> float:
    """当日已付手续费（paper_orders 权威账本，含开/平/部分平所有成交单）。"""
    acct = int(account_id)
    now = time.time()
    if use_cache:
        with _cache_lock:
            hit = _fees_cache.get(acct)
            if hit and hit[0] > now:
                return float(hit[1])
    from sqlalchemy import text as _t
    row = db.execute(_t(
        "SELECT COALESCE(SUM(COALESCE(fee, 0)), 0) FROM paper_orders "
        "WHERE account_id = :acct AND status = 'filled' AND created_at >= :day_start"
    ), {"acct": acct, "day_start": _local_day_start()}).first()
    fees = float(row[0] or 0) if row else 0.0
    with _cache_lock:
        _fees_cache[acct] = (now + _CACHE_TTL_S, fees)
    return fees


def invalidate_cache(account_id: Optional[int] = None) -> None:
    with _cache_lock:
        if account_id is None:
            _fees_cache.clear()
        else:
            _fees_cache.pop(int(account_id), None)


# ── live 账户：进程内按日累计（Phase 0 口径；Phase 2 OMS 对账后改读交易所 income）──
# key: (account_id, 'YYYY-MM-DD') -> fees
_live_fees: Dict[tuple, float] = {}


def _day_key() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def record_live_fee(account_id: int, fee: float) -> float:
    """live 成交后登记（估算或真值），返回当日累计。"""
    key = (int(account_id), _day_key())
    with _cache_lock:
        # 顺手清理旧日键，避免无限增长
        for k in [k for k in _live_fees if k[1] != key[1]]:
            _live_fees.pop(k, None)
        _live_fees[key] = float(_live_fees.get(key, 0.0)) + max(0.0, float(fee or 0))
        return _live_fees[key]


def fees_paid_today_live(account_id: int) -> float:
    with _cache_lock:
        return float(_live_fees.get((int(account_id), _day_key()), 0.0))


def check_fee_budget(
    db,
    account_id: int,
    *,
    est_notional: float,
    equity: float,
    fees_today: Optional[float] = None,
    source: str = "paper",
    fee_rate: Optional[float] = None,
) -> FeeBudgetVerdict:
    """新开/加仓前调用。返回 verdict；allowed=False 时调用方必须拒单。

    fees_today 为 None 时按 source 自动取数（paper → paper_orders）；live 由调用方注入。
    """
    pct = daily_fee_budget_pct()
    eq = float(equity or 0)
    est = estimate_fee(est_notional, fee_rate)
    if pct <= 0:
        return FeeBudgetVerdict(True, "fee_budget_disabled", 0.0, est, 0.0, eq, 0.0, pct, source)
    if eq <= 0:
        # 权益未知：不拦（由保证金/风控门兜底），但留痕
        return FeeBudgetVerdict(True, "equity_unknown", 0.0, est, 0.0, eq, 0.0, pct, source)
    try:
        if fees_today is None:
            fees_today = fees_paid_today_paper(db, account_id) if source == "paper" else 0.0
        fees_today = float(fees_today or 0)
        budget = eq * pct / 100.0
        spent = fees_today + est
        ratio = spent / eq
        if spent > budget:
            _stats["blocked"] += 1
            _stats["last_block"] = {
                "ts": time.time(), "account_id": int(account_id), "fees_today": round(fees_today, 4),
                "est_fee": round(est, 4), "budget": round(budget, 4), "equity": round(eq, 2),
            }
            return FeeBudgetVerdict(
                False,
                f"fee_budget_exceeded: today={fees_today:.4f}+est={est:.4f} > budget={budget:.4f} "
                f"({pct:.2f}% of equity {eq:.2f})",
                fees_today, est, budget, eq, ratio, pct, source,
            )
        _stats["allowed"] += 1
        return FeeBudgetVerdict(
            True, f"fee_budget_ok: {spent:.4f}/{budget:.4f}", fees_today, est, budget, eq, ratio, pct, source,
        )
    except Exception as exc:  # fail-open，但必须留痕
        _stats["errors"] += 1
        logger.warning("[FeeBudget] 检查异常(放行) acct=%s: %s", account_id, exc)
        return FeeBudgetVerdict(True, f"fee_budget_error: {exc}", 0.0, est, 0.0, eq, 0.0, pct, source)


def fee_budget_status(db, account_id: int, equity: Optional[float] = None) -> Dict[str, Any]:
    """ops 观测：当日费用 / 预算 / 剩余，供 /api/ops/edge-ledger 与看板展示。"""
    pct = daily_fee_budget_pct()
    fees_today = 0.0
    try:
        fees_today = fees_paid_today_paper(db, account_id, use_cache=False)
    except Exception as exc:
        logger.debug("[FeeBudget] status 取数失败: %s", exc)
    eq = float(equity or 0)
    if eq <= 0:
        try:
            from sqlalchemy import text as _t
            row = db.execute(_t(
                "SELECT total_equity FROM paper_balances WHERE account_id = :a"
            ), {"a": int(account_id)}).first()
            eq = float(row[0] or 0) if row else 0.0
        except Exception:
            eq = 0.0
    budget = eq * pct / 100.0 if eq > 0 and pct > 0 else 0.0
    return {
        "account_id": int(account_id),
        "budget_pct": pct,
        "equity": round(eq, 4),
        "fees_today": round(fees_today, 4),
        "budget": round(budget, 4),
        "remaining": round(max(0.0, budget - fees_today), 4),
        "used_ratio": round(fees_today / budget, 4) if budget > 0 else None,
        "counters": dict(_stats),
    }
