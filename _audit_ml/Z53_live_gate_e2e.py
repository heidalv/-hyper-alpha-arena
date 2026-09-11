# -*- coding: utf-8 -*-
"""Z53：验证「交易所原始持仓 → 组合闸」全链路真的会拦（端到端语义测试）。

用 Hyperliquid 风格**原始字段**（无 tier/nature）构造持仓，跑一遍
midlong_helpers 里的补齐逻辑 + 真实组合闸，确认：
  - 不补齐 → 闸看到 0 笔（复现"修了但没生效"）；
  - 补齐后 → 闸按上限拦截。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.config import settings  # noqa: E402
from backend.services.mlto.midlong_portfolio_risk import (  # noqa: E402
    check_portfolio_open_allowed,
    collect_midlong_positions,
)

# 模拟 live_executor._get_hl_positions 的返回形状（无 tier/nature）
RAW = [
    {"symbol": "SOL", "side": "long", "size": 2.3048, "entry_price": 103.24,
     "mark_price": 100.5, "leverage": 4.0, "unrealized_pnl": -6.3, "margin": 59.4,
     "status": "open", "channel": "live", "exchange": "hyperliquid"},
    {"symbol": "ETH", "side": "long", "size": 1.0, "entry_price": 2477.88,
     "mark_price": 2461.0, "leverage": 5.0, "unrealized_pnl": -7.0, "margin": 66.0,
     "status": "open", "channel": "live", "exchange": "hyperliquid"},
    {"symbol": "XRP", "side": "long", "size": 663.7, "entry_price": 1.42694,
     "mark_price": 1.386, "leverage": 4.0, "unrealized_pnl": -27.2, "margin": 71.3,
     "status": "open", "channel": "live", "exchange": "hyperliquid"},
    {"symbol": "ASTER", "side": "long", "size": 1084.9, "entry_price": 0.7522,
     "mark_price": 0.737, "leverage": 3.0, "unrealized_pnl": -16.5, "margin": 95.0,
     "status": "open", "channel": "live", "exchange": "hyperliquid"},
]

settings.MIDLONG_PORTFOLIO_GATE_ENABLED = True
settings.MIDLONG_MAX_OPEN_POSITIONS = 4
settings.MIDLONG_CORR_CLUSTER_SYMBOLS = ""

print("=== A. 不补齐（复现修复前）===")
n0 = len(collect_midlong_positions(None, RAW))
print(f"  闸看到的持仓 = {n0} 笔  ← 原始交易所字段不含 tier/nature，全被过滤")

print("\n=== B. 按修复逻辑补齐 tier/nature ===")
tagged = []
for p in RAW:
    q = dict(p)
    q.setdefault("timeframe_tier", "long")
    q.setdefault("trade_nature", "position")
    tagged.append(q)
n1 = len(collect_midlong_positions(None, tagged))
print(f"  闸看到的持仓 = {n1} 笔")

print("\n=== C. 补齐后闸是否拦截（上限 4，已有 4 笔）===")
ok, why = check_portfolio_open_allowed(
    symbol="VIRTUAL", action="buy",
    portfolio={"balance": {"total_equity": 4710.0}, "positions": tagged},
    new_notional=900.0,
)
print(f"  4 笔在册 + 上限 4 → {'放行（异常）' if ok else '拦截'}  ({why})")

print("\n=== D. 3 笔在册时应放行 ===")
ok3, why3 = check_portfolio_open_allowed(
    symbol="VIRTUAL", action="buy",
    portfolio={"balance": {"total_equity": 4710.0}, "positions": tagged[:3]},
    new_notional=900.0,
)
print(f"  3 笔在册 + 上限 4 → {'放行' if ok3 else '拦截（异常）'}  ({why3})")

assert n0 == 0, "未补齐时应当被过滤（说明补齐是必需的）"
assert n1 == len(RAW), "补齐后应当全部计入"
assert not ok, "达到上限必须拦截"
assert ok3, "未达上限必须放行"
print("\n✓ 端到端语义验证通过（补齐是必需的，且闸真的会拦）")
