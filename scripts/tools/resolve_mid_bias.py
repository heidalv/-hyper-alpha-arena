"""Resolve R066: is the engine's mid biased, or is the external tick DB lagging?

Facts so far:
  · mid_px == book_mid EXACTLY (6 legs, sd=0.00)
        -> the accounting uses the engine's own fresh book, internally consistent
  · mid_px vs EXTERNAL asterdex_book_ticker differs by +7.75bp (t=+4.03, n=140)

Both can be true. The question is whether the external DB is a FAIR benchmark.
If the external row matched by event_ts is systematically OLDER than the fill,
then the "bias" is a lag artifact of my query, not an engine defect.

This prints, per leg: fill ts, external row event_ts, the time gap, and both mids.
"""
from __future__ import annotations

import io
import statistics as st
import sys

import psycopg

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
CORE = "postgresql://laobao:alpha_pass@localhost:5432/alpha_arena"
MKT = "postgresql://laobao:alpha_pass@localhost:5432/alpha_market"


def main() -> int:
    with psycopg.connect(CORE, autocommit=True) as c, c.cursor() as cur:
        cur.execute("""
            SELECT ts, symbol, coalesce(meta_json->>'side',''),
                   (meta_json->>'mid_px')::float8,
                   (meta_json->>'book_mid')::float8,
                   coalesce(meta_json->>'fill_px','0')::float8
            FROM lane_ledger
            WHERE lane_id='mm_asterdex' AND event='fill'
              AND meta_json ? 'book_mid'
              AND (meta_json->>'book_mid')::float8 > 0
            ORDER BY ts
        """)
        rows = cur.fetchall()

    print("=" * 104)
    print("逐腿：引擎 mid vs 外部 tick 库 mid（含时间对齐）")
    print("=" * 104)
    print(f"  {'fill_ts':<10}{'sym':<9}{'side':<5}{'mid_px':>12}{'book_mid':>12}"
          f"{'ext_mid':>12}{'gap_ms':>9}{'ext_age_ms':>11}")
    gaps = []
    diffs = []
    with psycopg.connect(MKT, autocommit=True) as m, m.cursor() as mc:
        for ts, sym, side, mid, bm, fpx in rows:
            if mid <= 0 or bm <= 0:
                continue
            s = str(sym).upper()
            if not s.endswith("USDT"):
                s += "USDT"
            t0 = int(ts.timestamp() * 1000)
            # the row my R066 query would have picked: nearest event_ts
            mc.execute("""
                SELECT ((bid_px+ask_px)/2.0), event_ts_ms, ingest_ts
                FROM asterdex_book_ticker
                WHERE symbol=%s AND event_ts_ms BETWEEN %s AND %s
                ORDER BY abs(event_ts_ms - %s) ASC LIMIT 1
            """, (s, t0 - 3000, t0 + 3000, t0))
            r = mc.fetchone()
            if not r or r[0] is None or float(r[0]) <= 0:
                continue
            ext, ev_ts = float(r[0]), int(r[1])
            gap = ev_ts - t0
            gaps.append(gap)
            diffs.append((mid - ext) / ext * 1e4)
            print(f"  {ts.strftime('%H:%M:%S'):<10}{str(sym)[:8]:<9}{side:<5}"
                  f"{mid:>12.5f}{bm:>12.5f}{ext:>12.5f}{gap:>9}{abs(gap):>11}")

    print()
    print("=" * 104)
    print("统计")
    print("=" * 104)
    print(f"  n = {len(diffs)}")
    if gaps:
        print(f"  外部行 event_ts − fill ts : 均值 {st.mean(gaps):+.0f}ms  "
              f"中位 {st.median(gaps):+.0f}ms  |gap| 中位 {st.median([abs(g) for g in gaps]):.0f}ms")
    if diffs:
        print(f"  mid_px − ext_mid          : 均值 {st.mean(diffs):+.2f}bp  "
              f"SD {st.pstdev(diffs):.2f}")
    print()
    print("  判定指引：")
    print("   · 若 |gap| 很大（数百 ms 以上）⇒ 外部行与成交**不同刻**，")
    print("     用它的中价当基准会引入滞后偏差 ⇒ **R066 的偏置结论不成立**。")
    print("   · 若 gap≈0 而 mid 仍差很多 ⇒ 引擎的盘口**不是**交易所 bookTicker")
    print("     ⇒ 那才是真正的口径问题。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
