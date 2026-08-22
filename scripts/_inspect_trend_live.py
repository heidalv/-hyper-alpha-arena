# -*- coding: utf-8 -*-
"""实测 trend_follow / swing 活仓参数（持仓时长、TP/SL 冲击、退出通道）。"""
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
               size, partial_realized_pnl,
               tp_price, sl_price, leverage, symbol,
               EXTRACT(EPOCH FROM (closed_at - opened_at)) as hold_sec
        FROM paper_positions
        WHERE close_price IS NOT NULL AND entry_price IS NOT NULL AND entry_price > 0
          AND status = 'closed' AND trade_nature IN ('trend_follow', 'swing')
        ORDER BY closed_at DESC
    """)).fetchall()

print(f"越出 n={len(rows)}")
for r in rows:
    nature, side = r[0], r[1]
    ep, xp = float(r[2]), float(r[3])
    reason = r[4] or ""
    size = float(r[5] or 0)
    pnl = float(r[6] or 0)
    notional = size * ep
    pnl_pct = pnl / notional * 100
    tp, sl = r[7], r[8]
    hold = float(r[11] or 0) / 3600
    tp_pct = (float(tp) / ep - 1) * 100 if tp else 0
    sl_pct = (1 - float(sl) / ep) * 100 if sl else 0
    print(f"  {nature:<12} {side:<4} {r[11]:<6} {reason:<20} pnl={pnl_pct:+7.3f}% hold={hold:6.2f}h tp={tp_pct:+6.3f}% sl={sl_pct:+.3f}%")
