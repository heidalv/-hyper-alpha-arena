"""成交后动作可行性硬门（PostFill Position Agent · Phase 1）。

所有部分平仓 / 减仓动作在发单前必须过这里的最小名义金额双边检查与
手续费预算门，保证"决策层永远不发出交易所会拒的单"：

  1. minNotional 双边检查：平仓份额 ≥ 交易所最小名义，且平后剩余份额
     ≥ 最小名义。剩余不达标 → 判定 escalate_full（合并为全平）；份额
     不达标 → 判定 reject（放弃本次 reduce，档位不消费）。
  2. fee budget：本仓累计费用（分批已付 + 预估本腿）超过仓位名义的
     预算比例（EXIT_FEE_BUDGET_PCT，默认 15%）→ 禁止再发微操减仓腿。

最小名义来源：paper_exchange_simulator 的交易所规则表（与 paper 撑单
仿真同一口径）；可用 EXIT_MIN_NOTIONAL_OVERRIDE_USD 全局覆盖。
"""
from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


def resolve_min_notional_usd(exchange: Optional[str]) -> float:
    """该交易所的最小下单名义（USD）。覆盖项 > 交易所规则表 > 5.0 兜底。

    [2026-08-31] 规则表回退交易所改为币安（.env DEFAULT_EXCHANGE=binance）：
    未知/缺失交易所不再回退已停用的 hyperliquid（$10），统一按币安 $5。
    PAPER_EXCHANGE_RULES_FALLBACK 可覆盖。
    """
    _ov = os.getenv("EXIT_MIN_NOTIONAL_OVERRIDE_USD", "").strip()
    if _ov:
        try:
            _v = float(_ov)
            if _v > 0:
                return _v
        except ValueError:
            pass
    try:
        from backend.services.exchange.paper_exchange_simulator import (
            get_paper_exchange_rules,
        )
        return float(get_paper_exchange_rules(exchange or "").min_notional_usd or 5.0)
    except Exception:
        return 5.0


def check_partial_close_notional(
    *,
    exchange: Optional[str],
    chunk_qty: float,
    pos_qty: float,
    price: float,
) -> tuple[str, str]:
    """部分平仓的 minNotional 双边检查。

    返回 (verdict, detail)：
      "ok"            — 双边均达标，按原计划部分平仓
      "escalate_full" — 剩余份额将低于最小名义 → 应改为全平（防尘仓）
      "reject"        — 平仓份额本身低于最小名义 → 放弃本次部分平仓
      "unknown"       — 价格缺失等无法判定 → 放行（由下游仿真/交易所兜底）
    """
    chunk = float(chunk_qty or 0)
    total = float(pos_qty or 0)
    px = float(price or 0)
    if chunk <= 0 or total <= 0 or px <= 0:
        return "unknown", f"invalid qty/price chunk={chunk} total={total} px={px}"
    remaining = total - chunk
    if remaining < 0:
        remaining = 0.0
    min_notional = resolve_min_notional_usd(exchange)
    chunk_notional = chunk * px
    remaining_notional = remaining * px
    if chunk_notional < min_notional:
        return (
            "reject",
            f"chunk_notional=${chunk_notional:.2f} < min=${min_notional:.2f}",
        )
    if remaining_notional < min_notional:
        return (
            "escalate_full",
            f"remaining_notional=${remaining_notional:.2f} < min=${min_notional:.2f}",
        )
    return "ok", f"chunk=${chunk_notional:.2f} remaining=${remaining_notional:.2f} min=${min_notional:.2f}"


def fee_budget_exceeded(
    *,
    fees_paid: float,
    est_leg_fee: float,
    notional_now: float,
) -> tuple[bool, str]:
    """手续费预算门：微操腿的费用占比超预算 → True（禁止再减仓）。

    口径：（已付分批费用 + 本腿预估费用） / 当前名义。
    预算比例 EXIT_FEE_BUDGET_PCT（默认 0.15）。名义缺失时不拦（fail-open，
    由 minNotional 门兜底）。
    """
    try:
        _budget_pct = float(os.getenv("EXIT_FEE_BUDGET_PCT", "0.15") or 0.15)
    except ValueError:
        _budget_pct = 0.15
    if _budget_pct <= 0:
        return False, "budget disabled"
    notional = float(notional_now or 0)
    if notional <= 0:
        return False, "notional unknown"
    spent = float(fees_paid or 0) + float(est_leg_fee or 0)
    ratio = spent / notional
    if ratio > _budget_pct:
        return True, f"fee_ratio={ratio:.1%} > budget={_budget_pct:.0%} (paid={fees_paid:.4f} est={est_leg_fee:.4f})"
    return False, f"fee_ratio={ratio:.1%} <= budget={_budget_pct:.0%}"
