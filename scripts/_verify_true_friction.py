# -*- coding: utf-8 -*-
"""确认真实单笔摩擦：join paper_positions × position_exit_events（最终平仓事件）。

列序: 0=id 1=nature 2=side 3=entry 4=close 5=size 6=close_reason
      7=partial_realized_pnl 8=Σevent.pnl 9=Σevent.fee 10=n_events
  raw_move_pnl = (close-entry)*sign*size
  net_pnl      = Σ event.pnl（含费后的实际记账）
  fee_total    = Σ event.fee
  slip_implied = raw_move_pnl - net_pnl - fee_total
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
        SELECT p.id, p.trade_nature, p.side, p.entry_price, p.close_price, p.size,
               p.close_reason, p.partial_realized_pnl,
               COALESCE(SUM(e.pnl), 0) as event_pnl,
               COALESCE(SUM(e.fee), 0) as event_fee,
               COUNT(e.id) as n_events
        FROM paper_positions p
        LEFT JOIN position_exit_events e ON e.position_id = p.id
        WHERE p.status = 'closed' AND p.close_price IS NOT NULL AND p.entry_price > 0
          AND p.trade_nature = 'scalp'
        GROUP BY p.id
        ORDER BY p.closed_at DESC
        LIMIT 60
    """)).fetchall()

print(f"{'id':>5} {'side':<5} {'move%':>7} {'pnl%':>7} {'fees%':>7} {'evt':>4} {'slip%':>7} {'reason':<25}")
per_trade_slip = []
for r in rows:
    pid = r[0]; side = str(r[2] or "long").lower()
    ep, xp = float(r[3] or 0), float(r[4] or 0)
    size = float(r[5] or 0)
    if ep <= 0 or xp <= 0 or size <= 0:
        continue
    sign = 1.0 if side.startswith("buy") else -1.0
    raw = (xp - ep) * sign * size
    event_pnl = float(r[8] or 0)
    event_fee = float(r[9] or 0)
    n_events = int(r[10] or 0)
    total_pnl = event_pnl
    slip = raw - total_pnl - event_fee
    per_trade_slip.append(slip / (size * ep) * 100)
    print(f"{pid:>5} {side:<5} {raw/(size*ep)*100:>+6.3f}% {total_pnl/(size*ep)*100:>+6.3f}% "
          f"{event_fee/(size*ep)*100:>+6.3f}% {n_events:>4} {slip/(size*ep)*100:>+6.3f}% {(r[6] or '')[:25]}")

if per_trade_slip:
    arr = np.array(per_trade_slip)
    print(f"\n  slip(+fee差) 占本金%: 样本={len(arr)} 中位={np.median(arr):+.3f}% 均值={arr.mean():+.3f}% q90={np.percentile(arr,90):+.3f}%")
    # 只统计事件数≥1（真有事件行）且无 add 的
    clean = [v for r, v in zip(rows, per_trade_slip) if int(r[10] or 0) > 0]
    if clean:
        arr2 = np.array(clean)
        print(f"  仅含事件的样本: n={len(arr2)} 中位={np.median(arr2):+.3f}% 均值={arr2.mean():+.3f}% q90={np.percentile(arr2,90):+.3f}%")
