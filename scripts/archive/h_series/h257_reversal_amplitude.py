# -*- coding: utf-8 -*-
"""H257 反转 alpha 的幅度依赖：|趋势| 越大，逆势−顺势差越大吗？

# 决定 side_trend_min_bp（counter_trend 的触发下限）

H256 已证：lookback=120s 时逆势 +0.84 / 顺势 −2.38 / 差 3.22bp，13/13 小时。
但 counter_trend 实现里有个门槛 `side_trend_min_bp`：
    |趋势| < min_bp ⇒ 仍双边挂（防噪声把车道切成单边）

所以需要回答：**反转 alpha 是随 |趋势| 单调增强，还是在所有幅度都一样？**

· 若随幅度单调增强 ⇒ min_bp 该设高（只在强趋势时单边，避开噪声）
· 若所有幅度都反转（甚至小趋势反转更强）⇒ min_bp 设 0 或很小

# 本脚本

lookback 固定 120s（H256 最强），按 |trend| 分档，看：
  逆势 net_bp、顺势 net_bp、差、以及每档的腿数占比

# 用法

    python scripts/h257_reversal_amplitude.py --hours 12
"""
from __future__ import annotations

import argparse
import json
import pathlib
import statistics as st
import sys
from datetime import datetime

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "research_l1" / "out" / "h257_reversal_amplitude.json"
LANE = "mm_asterdex"
CUR = ["ASTERUSDT", "XRPUSDT", "SOLUSDT", "HYPEUSDT", "ADAUSDT"]
K = 120.0


def dsn() -> str:
    env = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    url = env["DATABASE_URL"]
    for j in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(j, "")
    return url


def market_dsn() -> str:
    return dsn().rsplit("/", 1)[0] + "/alpha_market"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=12.0)
    a = ap.parse_args()

    import psycopg
    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            cur.execute("""
                SELECT symbol, ts, coalesce(meta_json->>'side',''),
                       coalesce(net_bp,0), coalesce(notional,0)
                FROM lane_ledger
                WHERE lane_id=%s AND (meta_json->'flatten')::text='false'
                  AND ts >= now() - (%s || ' hours')::interval
                  AND coalesce(notional,0) > 0
                ORDER BY ts ASC
            """, (LANE, str(float(a.hours))))
            legs = cur.fetchall()

    PX = {}
    for sym in CUR:
        try:
            with psycopg.connect(market_dsn()) as c:
                with c.cursor() as cur:
                    cur.execute("""
                        SELECT DISTINCT ON (bucket) bucket, bid_px, ask_px
                        FROM (SELECT (event_ts_ms/1000) AS bucket, bid_px, ask_px
                              FROM asterdex_book_ticker
                              WHERE ingest_ts >= now() - (%s || ' hours')::interval
                                AND symbol = %s AND bid_px>0 AND ask_px>bid_px) t
                        ORDER BY bucket, bid_px
                    """, (str(float(a.hours) + 0.3), sym))
                    PX[sym] = cur.fetchall()
        except Exception as e:
            print(f"  ⚠️ {sym}: {str(e)[:60]}")

    print("=" * 104)
    print(f"H257  反转 alpha 的幅度依赖（lookback={K:g}s）")
    print("=" * 104)

    rows = []
    for sym, ts, side, net, notl in legs:
        tk = str(sym).upper()
        if not tk.endswith("USDT"):
            tk += "USDT"
        if tk not in PX:
            continue
        d = {int(b): ((float(x) + float(y)) / 2.0) for b, x, y in PX[tk]}
        ks = sorted(d)
        import bisect
        t = int(ts.timestamp())
        i = bisect.bisect_right(ks, t) - 1
        j = i
        while j > 0 and ks[i] - ks[j] < K:
            j -= 1
        if j == i or d[ks[j]] <= 0:
            continue
        trend = (d[ks[i]] - d[ks[j]]) / d[ks[j]] * 1e4
        up = trend > 0
        is_ct = (side == "buy" and not up) or (side == "sell" and up)
        rows.append({"trend": trend, "net": float(net), "ct": is_ct,
                     "notional": float(notl)})

    print(f"\n  匹配 {len(rows)} 条腿")
    tr = sorted(abs(r["trend"]) for r in rows)
    n = len(tr)
    print(f"  |趋势| 分布：P25 {tr[n//4]:.2f}　P50 {tr[n//2]:.2f}　"
          f"P75 {tr[3*n//4]:.2f}　P90 {tr[int(n*0.9)]:.2f} bp")

    EDGES = [0, 2, 5, 8, 12, 20, 1e9]
    print(f"\n{'━'*104}\n  按 |趋势| 分档（lookback={K:g}s）\n{'━'*104}")
    print(f"\n  {'|趋势|档(bp)':>14}{'腿数':>7}{'占比':>7}{'逆势net':>10}"
          f"{'顺势net':>10}{'差':>9}{'逆势为正占比':>13}")
    for i in range(len(EDGES) - 1):
        lo, hi = EDGES[i], EDGES[i + 1]
        sel = [r for r in rows if lo <= abs(r["trend"]) < hi]
        ct = [r["net"] for r in sel if r["ct"]]
        mt = [r["net"] for r in sel if not r["ct"]]
        if len(sel) < 30:
            continue
        mct = st.mean(ct) if ct else 0.0
        mmt = st.mean(mt) if mt else 0.0
        pos = sum(1 for x in ct if x > 0) / len(ct) * 100 if ct else 0.0
        lbl = f"{lo:g}–{hi:g}" if hi < 1e9 else f"{lo:g}+"
        print(f"  {lbl:>14}{len(sel):>7}{len(sel)/n*100:>6.1f}%{mct:>+10.4f}"
              f"{mmt:>+10.4f}{mct-mmt:>+9.4f}{pos:>12.1f}%")

    # 结论：min_bp 建议
    print(f"\n{'━'*104}\n  结论\n{'━'*104}")
    print(f"\n  若「差」随 |趋势| 单调增大 ⇒ min_bp 设高（只在强趋势单边）")
    print(f"  若小趋势档「差」也显著为正 ⇒ min_bp 设 0~3（全档单边）")
    print(f"\n  ⇒ 判断基准：差 > +1.0bp 且逆势为正占比 > 60% 的档，就是该单边的区间")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"hours": a.hours, "k": K, "n": len(rows)},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  写出 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
