# -*- coding: utf-8 -*-
"""[交易分析 R10] `exit_policy:min_roi_decay` 的反事实：若不衰减平仓、继续持有 6/12/24h 会怎样？

口径与保真度：
- 用行情库的 1h K 线；**同时检查这段时间内是否触及原 SL**：触及则按 SL 价成交（更忠实的模拟）；
- 手续费按原单的实际费用沿用（两次情形相同）⇒ 差异纯粹来自价格路径；
- 粒度限制：1h bar 内的瞬时插针可能漏判（已在结论中注明）。
"""
from __future__ import annotations

import sys

sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")

from sqlalchemy import text  # noqa: E402

from backend.database.connection import SessionLocal, market_engine  # noqa: E402


def mq(sql, **kw):
    with market_engine.connect() as c:
        return c.execute(text(sql), kw).fetchall()


tabs = [r[0] for r in mq(
    "SELECT table_name FROM information_schema.tables WHERE table_schema='public' "
    "AND table_name LIKE '%kline%' ORDER BY table_name"
)]
big = None
for t in tabs:
    try:
        pcol = [r[0] for r in mq(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name=:t AND column_name IN ('period','interval')", t=t
        )]
        if not pcol:
            continue
        n = mq(f"SELECT count(*) FROM {t} WHERE {pcol[0]}='1h'")[0][0]
        if n > 1_000_000:
            big = (t, pcol[0])
            break
    except Exception:
        continue
print("使用 1h K 线表:", big)
if not big:
    print("未找到 1h K 线表，退出")
    raise SystemExit(1)
tbl, pcol = big
tcol = "timestamp"
cols = [r[0] for r in mq(
    "SELECT column_name FROM information_schema.columns WHERE table_name=:t", t=tbl
)]
for cand in ("timestamp_ms", "open_time", "ts_ms", "ts", "open_time_ms"):
    if cand in cols:
        tcol = cand
        break
price_map = {k: k for k in ("close", "close_price", "c") if k in cols}
ccol = next(iter(price_map)) if price_map else "close"
lcol = "low" if "low" in cols else ("low_price" if "low_price" in cols else None)
print(f"  {tbl}.{tcol} / {ccol} / {lcol}  (列={cols[:12]}…)")

# [R10 修复] `crypto_klines.timestamp` 是**整数 epoch**（不是 timestamp 列）：
# 直接比较会报 "integer > timestamp without time zone"。按量级判断秒/毫秒。
_probe = mq(f"SELECT {tcol} FROM {tbl} WHERE {pcol}='1h' ORDER BY {tcol} DESC LIMIT 1")
_ts = int(_probe[0][0]) if _probe else 0
_ms = _ts > 10_000_000_000
print(f"  timestamp 量级样例={_ts} ⇒ {'毫秒' if _ms else '秒'}")


def _to_epoch(dt):
    import datetime as _dt
    base = dt.replace(tzinfo=_dt.timezone.utc) if dt.tzinfo is None else dt
    v = base.timestamp()
    return int(v * 1000) if _ms else int(v)

with SessionLocal() as s:
    rows = s.execute(text("""
        SELECT id, symbol, lower(side) side, entry_price, close_price, sl_price, size,
               closed_at, margin,
               ((CASE WHEN lower(side) IN ('long','buy') THEN (close_price-entry_price)
                      ELSE (entry_price-close_price) END) * size)
               - coalesce(partial_fee_paid,0) - coalesce(final_fee_paid,0) net,
               coalesce(partial_fee_paid,0) + coalesce(final_fee_paid,0) fee
        FROM paper_positions
        WHERE status='closed' AND closed_at >= '2026-09-15'
          AND close_reason LIKE 'exit_policy:min_roi_decay%'
        ORDER BY closed_at
    """)).fetchall()

print(f"\n样本：min_roi_decay 平仓 {len(rows)} 笔")
actual_total = sum(float(r[9]) for r in rows)

for H in (6, 12, 24):
    tot = 0.0
    sl_hit = 0
    better = worse = 0
    for r in rows:
        sym, side, entry, sl, size, closed, fee = (
            r[1], r[2], float(r[3]), float(r[4]) if r[5] is None else float(r[5]),
            float(r[6]), r[7], float(r[10]),
        )
        bars = mq(
            f"SELECT {lcol}, {ccol} FROM {tbl} WHERE symbol=:s AND {pcol}='1h' "
            f"AND {tcol} > :a AND {tcol} <= :b ORDER BY {tcol}",
            s=sym, a=_to_epoch(closed),
            b=_to_epoch(closed + __import__("datetime").timedelta(hours=H)),
        )
        if not bars:
            hyp = float(r[9])
        else:
            stop = False
            for lo, _cl in bars:
                if lo is not None and ((side in ("long", "buy") and float(lo) <= sl) or
                                       (side in ("short", "sell") and float(lo) >= sl)):
                    stop = True
                    break
            px = sl if stop else float(bars[-1][1])
            sl_hit += 1 if stop else 0
            gross = (px - entry) * size if side in ("long", "buy") else (entry - px) * size
            hyp = gross - fee
        tot += hyp
        if hyp > float(r[9]):
            better += 1
        else:
            worse += 1
    print(f"  继续持有 +{H:2d}h: 反事实净={tot:8.2f}（实际={actual_total:8.2f}, 差={tot-actual_total:+8.2f}）"
          f"  其中触及原SL={sl_hit}笔  更好={better} 更差={worse}")
