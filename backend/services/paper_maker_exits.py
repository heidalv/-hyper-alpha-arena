"""[2026-09-23 设计2 D5] 挂单出场的费率路由（纸面层，只改费率口径、不改成交时点/价格）。

依据：docs/长线出场方向_实施设计_20260923.md §2 —— 可等待的出场按 maker 费率（asterdex=0%），
风险出口永远 taker。开关 PAPER_MAKER_EXITS（默认 false）。回滚=改 false + 重启。
注意：成交仍走 MARKET 仿真（保持现有时点/价格/滑点），仅费率按 maker 档计——这是设计明确的口径
（"平仓时点/价格不得改变，仅费率口径变"）。未来若做真 post-only 挂单（可能不成交）另立开关。
"""
from __future__ import annotations

import os

# 可等待出场的 close_reason 前缀白名单（风险出口一律不在内：sl/stop_loss/trailing/liquidation/
# force_close/emergency_drawdown/profit_drawdown*/trend_*/thesis_*/breakeven_tp/min_roi_decay 等）
_MAKER_PREFIXES = (
    "tp", "staged_tp", "partial", "max_hold_timeout", "dust", "symbol_removed",
    "rebalance", "rule_exit", "manual",
)


def enabled() -> bool:
    return (os.getenv("PAPER_MAKER_EXITS", "false") or "false").strip().lower() in ("1", "true", "yes", "on")


def eligible(reason: str) -> bool:
    r = str(reason or "").lower()
    return any(r.startswith(p) for p in _MAKER_PREFIXES)
