# -*- coding: utf-8 -*-
"""实测已平仓交易的往返成本（价格移动 vs 记账净 pnl 的差值）。

paper_positions：entry_price/close_price/trade_nature/close_reason/size/pnl
  move = (close/entry - 1) * dir_sign
  pct_net = pnl / (size * entry_price)   # 净收益率（含手续费+滑点）
  cost = move - pct_net                  # >0 = 摩擦成本
按 nature × close_reason 分组输出中位/均值/分位数。
"""
import sys
import numpy as np
sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")
from backend.core.tenant import set_system_identity
set_system_identity()

from backend.database.connection import SessionLocal
from sqlalchemy import text

with SessionLocal() as db:
    rows = db.execute(text("""
        SELECT trade_nature, side, entry_price, close_price, close_reason,
               size, partial_realized_pnl, leverage, symbol, closed_at
        FROM paper_positions
        WHERE close_price IS NOT NULL AND entry_price IS NOT NULL AND entry_price > 0
          AND status = 'closed'
        ORDER BY closed_at DESC
        LIMIT 20000
    """)).fetchall()

by_nature = {}
for r in rows:
    nature = (r[0] or "unknown")
    side = str(r[1] or "long").lower()
    ep = float(r[2]); xp = float(r[3]); size = float(r[5] or 0); pnl = float(r[6] or 0)
    if ep <= 0 or xp <= 0 or size <= 0:
        continue
    sign = 1.0 if side.startswith("buy") else -1.0
    move = (xp / ep - 1.0) * sign
    notional = size * ep
    pct_net = pnl / notional
    cost = move - pct_net
    by_nature.setdefault(nature, []).append((cost, move, pct_net, str(r[4] or ""), str(r[7] or "")))

print(f"总记录: {len(rows)}")
print(f"{'nature':<16}{'n':>6} {'cost中位':>9} {'cost均值':>9} {'p10':>8} {'p90':>8} {'move中位':>9}")
order = sorted(by_nature.items(), key=lambda kv: -len(kv[1]))
for nature, items in order:
    c = np.array([x[0] for x in items])
    m = np.array([x[1] for x in items])
    q = np.percentile(c, [10, 50, 90])
    print(f"  {nature:<16}{len(items):>6} {q[1]*100:>8.3f}% {c.mean()*100:>8.3f}% {q[0]*100:>7.3f}% {q[2]*100:>7.3f}% {np.median(m)*100:>8.3f}%")

print("\n  cost 按 nature × reason 分组:")
kw_map = {}
for nature, items in by_nature.items():
    for c, m, p, reason, sym in items:
        low = reason.lower()
        if any(k in low for k in ("sl", "stop_loss", "止损")):
            key = "SL"
        elif any(k in low for k in ("tp", "take_profit", "止盈")):
            key = "TP"
        elif "timeout" in low or "超时" in low:
            key = "TIMEOUT"
        elif "breakeven" in low or "break_even" in low:
            key = "BE"
        else:
            key = "OTHER"
        kw_map.setdefault((nature, key), []).append(c)
for (nature, key), vals in sorted(kw_map.items(), key=lambda x: -len(x[1])):
    v = np.array(vals)
    print(f"  {nature:<16} {key:<8} n={len(v):>5} med={np.median(v)*100:+.3f}% mean={v.mean()*100:+.3f}%")
