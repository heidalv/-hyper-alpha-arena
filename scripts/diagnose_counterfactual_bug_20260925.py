# -*- coding: utf-8 -*-
"""[交易分析 R12] 反事实的原始数据对照：逐笔打印，找 bug。"""
from __future__ import annotations

import datetime as _dt
import sys

sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")

from sqlalchemy import text  # noqa: E402

from backend.database.connection import SessionLocal, market_engine  # noqa: E402


def mq(sql, **kw):
    with market_engine.connect() as c:
        return c.execute(text(sql), kw).fetchall()


def ep(dt):
    return int(dt.timestamp())


with SessionLocal() as s:
    rows = s.execute(text("""
        SELECT symbol, lower(side), entry_price, close_price, sl_price, size, closed_at,
               ((CASE WHEN lower(side) IN ('long','buy') THEN (close_price-entry_price)
                      ELSE (entry_price-close_price) END) * size)
               - coalesce(partial_fee_paid,0) - coalesce(final_fee_paid,0) net,
               close_reason
        FROM paper_positions
        WHERE status='closed' AND closed_at >= '2026-09-15'
        ORDER BY closed_at
    """)).fetchall()

picks = []
for want in ("exit_policy:min_roi_decay", "sl", "tp"):
    for r in rows:
        if str(r[8]).startswith(want):
            picks.append((want, r))
            break

for tag, r in picks:
    sym, side, entry, close, slp, size, closed = (r[0], r[1], float(r[2]), float(r[3]),
                                                  r[4], float(r[5]), r[6])
    print(f"\n=== {tag} | {sym} {side} size={size} entry={entry:.6g} close={close:.6g} "
          f"sl={slp} closed={closed} 实际净={float(r[7]):.2f}")
    print(f"    名义 = size × entry = {size*entry:.1f}")
    for ex in ("asterdex", "binance"):
        bars = mq("SELECT timestamp, low_price, close_price FROM crypto_klines "
                  "WHERE symbol=:s AND period='1h' AND exchange=:ex "
                  "AND timestamp > :a AND timestamp <= :b ORDER BY timestamp LIMIT 30",
                  s=sym, ex=ex, a=ep(closed), b=ep(closed + _dt.timedelta(hours=24)))
        if not bars:
            print(f"    [{ex}] 无数据")
            continue
        first = bars[0]
        last = bars[-1]
        f_ts = _dt.datetime.utcfromtimestamp(int(first[0]))
        l_ts = _dt.datetime.utcfromtimestamp(int(last[0]))
        print(f"    [{ex}] bars={len(bars)}  首: {f_ts} low={first[1]} close={first[2]} | "
              f"末: {l_ts} low={last[1]} close={last[2]}")
        for px_label, px in (("首bar close", float(first[2])), ("末bar close", float(last[2]))):
            gross = (px - entry) * size if side in ("long", "buy") else (entry - px) * size
            print(f"      按{px_label}({px:.6g}) → 毛={gross:8.2f}（vs 实际毛={float(r[7]):8.2f}）")
