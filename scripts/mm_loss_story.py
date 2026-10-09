# -*- coding: utf-8 -*-
"""[F228] 把 -$13.30 的**每一笔**从账本里拉出来，用大白话讲清楚这笔亏损怎么来的。
只读。输出：SOL/BNB 在 22:25~23:00 的逐笔成交（挂单侧、成交价、数量、名义、
是否平仓腿、逆选择 bp），以及滚动净仓。
"""
import sys
sys.path.insert(0, "D:/001Alpha/Hyper-Alpha-Arena")

from sqlalchemy import text  # noqa: E402
from backend.database.connection import SessionLocal  # noqa: E402

SINCE = "2026-09-15T22:24:00+08:00"

with SessionLocal() as db:
    rows = db.execute(text(
        "SELECT ts, symbol, event, notional, spread_bp, price_bp, fee_bp, net_bp, meta_json"
        " FROM lane_ledger WHERE lane_id='mm_asterdex' AND ts >= CAST(:s AS timestamptz)"
        " ORDER BY ts ASC"), {"s": SINCE}).mappings().all()

print("== 逐笔成交（SOL/BNB）==")
print(f"{'时间':<13}{'币':<5}{'侧':<5}{'成交价':>10}{'数量':>12}{'名义$':>9}{'平仓?':>6}{'逆选择bp':>9}")
pos = {}
for r in rows:
    s = r["symbol"]
    if s not in ("SOL", "BNB"):
        continue
    m = r["meta_json"] or {}
    side = str(m.get("side") or "")[:4]
    q = float(m.get("qty") or 0.0)
    px = float(m.get("fill_px") or 0.0)
    fl = "★平仓" if str(m.get("flatten") or "") in ("true", "True", "1") else ""
    signed = q if side in ("buy",) else -q
    pos[s] = pos.get(s, 0.0) + signed
    t = r["ts"].strftime("%H:%M:%S")
    print(f"{t:<13}{s:<5}{side:<5}{px:>10.4f}{q:>12.4f}{abs(q)*px:>9.1f}{fl:>6}{r['price_bp']:>9.2f}"
          f"  → 净仓 {pos[s]:+.4f}")

print("\n== 两币最终净仓与已实现盈亏 ==")
for s in ("SOL", "BNB"):
    print(f"  {s}: 净仓 {pos.get(s, 0):+.4f}（应为 0 = 已平）")
