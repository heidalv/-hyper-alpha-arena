# -*- coding: utf-8 -*-
"""UNI 反复止损排查（只读）：往返明细 + 止损幅度 + 重开间隔（有无冷却）。"""
from __future__ import annotations

import io
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text as _text  # noqa: E402

from backend.database.connection import SessionLocal  # noqa: E402

db = SessionLocal()
rows = db.execute(_text("""
    SELECT id, side, strategy_id, trade_nature, filled_price, price, quantity,
           leverage, sl_price, tp_price, pnl, close_reason, status, filled_at, created_at, entry_price
      FROM paper_orders
     WHERE symbol = 'UNI'
     ORDER BY id DESC LIMIT 20
""")).fetchall()

print("=" * 118)
print("【UNI 订单流水（倒序）】")
print("=" * 118)
print(f"  {'id':>5} {'方向':4} {'性质':10} {'成交价':>9} {'数量':>8} {'杠杆':>4} "
      f"{'SL':>9} {'TP':>8} {'盈亏':>9} {'平仓原因':12} {'时间':20}")
ops = []
for r in reversed(rows):
    t = r[13] or r[14]
    print(f"  {r[0]:>5} {r[1]:4} {str(r[3] or '-'):10} "
          f"{(r[4] or 0):>9.4f} {(r[6] or 0):>8.3f} {str(r[7] or '-'):>4} "
          f"{(r[8] or 0):>9.4f} {(r[9] or 0):>8.3f} {(r[10] or 0):>9.3f} "
          f"{str(r[11] or '-'):12} {t.strftime('%m-%d %H:%M:%S') if t else '-':20}")
    ops.append({"t": t, "side": r[1], "chg": (r[4] or 0), "pnl": (r[10] or 0),
                "reason": r[11], "src": r[2], "nature": r[3]})

print()
print("=" * 118)
print("【往返与重开节奏】")
print("=" * 118)
opens = [o for o in ops if o["side"] == "buy"]
closes = [o for o in ops if o["side"] == "sell"]
print(f"  买入 {len(opens)} 笔 / 卖出 {len(closes)} 笔")
prev_close = None
for o in ops:
    if o["side"] == "buy" and prev_close is not None:
        gap = (o["t"] - prev_close["t"]).total_seconds() / 60 if o["t"] and prev_close["t"] else None
        print(f"  止损({prev_close['t']:%H:%M}) → 重开({o['t']:%H:%M})  间隔 {gap:6.1f} 分钟"
              f"   来源={o['src']} 性质={o['nature']}" if gap is not None else "")
    if o["side"] == "sell":
        prev_close = o

print()
print("=" * 118)
print("【每笔止损幅度 + 累计】")
print("=" * 118)
tot = 0.0
entry = None
for o in ops:
    if o["side"] == "buy":
        entry = o
    elif o["side"] == "sell" and entry is not None:
        pct = (o["chg"] - entry["chg"]) / entry["chg"] * 100 if entry["chg"] else 0
        tot += (o["pnl"] or 0)
        lev = 3
        print(f"  入场 {entry['chg']:.4f} → 出场 {o['chg']:.4f}  价格 {pct:+.2f}%"
              f"  ×{lev}x ⇒ 保证金 {pct*lev:+.2f}%   盈亏 {o['pnl']:+.2f}  原因={o['reason']}")
        entry = None
print(f"  UNI 这段累计盈亏 = {tot:+.2f}")

print()
print("=" * 118)
print("【近 24h 全币种止损平仓统计（看是否 UNI 特有）】")
print("=" * 118)
rows2 = db.execute(_text("""
    SELECT symbol, count(*) n, round(sum(pnl)::numeric, 2) s
      FROM paper_orders
     WHERE close_reason LIKE '%stop%' OR close_reason LIKE '%sl%' OR close_reason = '止损平仓'
     GROUP BY symbol ORDER BY n DESC LIMIT 12
""")).fetchall()
for r in rows2:
    print(f"  {r[0]:>10}  止损 {r[1]:>3} 次   合计 {r[2]}")
db.close()
