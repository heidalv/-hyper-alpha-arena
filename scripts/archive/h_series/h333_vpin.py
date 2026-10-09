# -*- coding: utf-8 -*-
"""H333 VPIN 毒性监测：用我们自己的成交流估计 VPIN，检验其对成交 markout 的预测力。

# 模型（Easley-López de Prado-O'Hara 2012 的实用近似）
   以 15s 桶的主动买卖失衡 |OFI| = |V_B−V_S|/(V_B+V_S) 为输入，
   VPIN_N = 过去 N 桶的 |OFI| 滚动均值（N=20 ≈ 5 分钟、N=80 ≈ 20 分钟）。
   检验：VPIN 高 ⇒ 我们的被动成交 markout 更差？（若是 ⇒ VPIN 门可部署，
   线上 runner 已有 seg_buy/seg_sell 流，加一个滚动均值即可热采用）

# 用法: python scripts/h333_vpin.py [--hours 48]
"""
from __future__ import annotations

import argparse
import bisect
import json
import pathlib
import sys
from collections import deque

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "research_l1" / "out" / "h333_vpin.json"
WINS = [20, 80]


def read_env_dsn(market: bool) -> str:
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
    return url.replace("/alpha_arena", "/alpha_market") if market else url


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=48.0)
    a = ap.parse_args()

    import psycopg

    with psycopg.connect(read_env_dsn(False)) as c:
        with c.cursor() as cur:
            cur.execute("""
                SELECT symbol, (meta_json->>'side') AS side,
                       (meta_json->>'fill_px')::float8, (meta_json->>'mid_px')::float8,
                       (meta_json->>'qty')::float8, ts
                FROM lane_ledger
                WHERE lane_id='mm_asterdex' AND event='fill'
                  AND ts >= now() - make_interval(secs => %s)
                  AND meta_json ? 'fill_px'
                ORDER BY ts
            """, (a.hours * 3600.0,))
            fills = cur.fetchall()
    print(f"我们的成交 {len(fills)} 笔（近 {a.hours:g}h）")

    syms = sorted({f[0] for f in fills})
    series, vpin = {}, {}
    with psycopg.connect(read_env_dsn(True)) as c:
        with c.cursor() as cur:
            for s in syms:
                bs = s if s.endswith("USDT") else s + "USDT"
                cur.execute("""
                    SELECT (event_ts_ms/1000)::bigint AS b,
                           (array_agg(bid_px ORDER BY event_ts_ms DESC))[1] AS bid,
                           (array_agg(ask_px ORDER BY event_ts_ms DESC))[1] AS ask
                    FROM asterdex_book_ticker
                    WHERE symbol=%s AND event_ts_ms >= (extract(epoch from now())*1000 - %s*3600*1000)::bigint
                      AND bid_px>0 AND ask_px>bid_px
                    GROUP BY b ORDER BY b
                """, (bs, a.hours + 1))
                rows = cur.fetchall()
                series[s] = ([int(r[0]) for r in rows],
                             [(float(r[1]) + float(r[2])) / 2.0 for r in rows])
                bare = bs[:-4] if bs.endswith("USDT") else bs
                cur.execute("""
                    SELECT timestamp, COALESCE(taker_buy_notional,0), COALESCE(taker_sell_notional,0)
                    FROM market_trades_aggregated
                    WHERE symbol=%s AND timestamp >= (extract(epoch from now())*1000 - %s*3600*1000)::bigint
                    ORDER BY timestamp
                """, (bare, a.hours + 1))
                orows = cur.fetchall()
                # VPIN 滚动：每 15s 桶的 |OFI|
                bmap = {}
                for ts_ms, bn, sn in orows:
                    tot = float(bn) + float(sn)
                    if tot > 0:
                        bmap[int(ts_ms) // 15000] = abs(float(bn) - float(sn)) / tot
                vp = {w: {} for w in WINS}
                buckets = sorted(bmap)
                for i, b in enumerate(buckets):
                    for w in WINS:
                        lo = max(0, i - w + 1)
                        vp[w][b] = sum(bmap[buckets[j]] for j in range(lo, i + 1)) / (i - lo + 1)
                vpin[s] = vp

    def mid_at(s, t):
        ks, ms = series[s]
        j = bisect.bisect_right(ks, t) - 1
        if j < 0 or t - ks[j] > 5:
            return None
        return ms[j]

    recs = []
    for sym, side, px, mid0, qty, ts in fills:
        if sym not in series or not px or not mid0:
            continue
        t0 = int(ts.timestamp())
        sign = 1.0 if (side or "").lower() == "buy" else -1.0
        m30 = mid_at(sym, t0 + 30)
        m300 = mid_at(sym, t0 + 300)
        if m30 is None or m300 is None:
            continue
        r = {"notional": abs((qty or 0.0) * px),
             "mk30": (m30 - px) / mid0 * 1e4 * sign,
             "mk300": (m300 - px) / mid0 * 1e4 * sign}
        for w in WINS:
            r[f"vpin{w}"] = vpin[sym][w].get(t0 // 15)
        if all(r[f"vpin{w}"] is not None for w in WINS):
            recs.append(r)
    n = len(recs)
    print(f"可用样本 {n} 笔\n")

    def wmean(sub, key):
        tot = sum(r["notional"] for r in sub) or 1.0
        return sum(r[key] * r["notional"] for r in sub) / tot

    out = {}
    for w in WINS:
        vals = sorted(r[f"vpin{w}"] for r in recs)
        t1, t2 = vals[n // 3], vals[2 * n // 3]
        print(f"VPIN_{w} 三分位 × markout（高 VPIN = 高毒性环境）")
        print(f"  {'分位':<18} {'n':>5} {'mk30':>9} {'mk300':>10}")
        rows = []
        for lab, lo, hi in (("低(<%.3f)" % t1, -1.0, t1), ("中", t1, t2), ("高(>%.3f)" % t2, t2, 2.0)):
            sub = [r for r in recs if lo <= r[f"vpin{w}"] < hi]
            if not sub:
                continue
            mk30, mk300 = wmean(sub, "mk30"), wmean(sub, "mk300")
            rows.append({"tier": lab, "n": len(sub), "mk30": round(mk30, 3), "mk300": round(mk300, 3)})
            print(f"  {lab:<18} {len(sub):>5} {mk30:>+9.3f} {mk300:>+10.3f}")
        out[f"vpin{w}"] = rows
    # 线性：VPIN 对 mk30 的斜率
    import statistics
    xs = [r["vpin20"] for r in recs]
    ys = [r["mk30"] for r in recs]
    mx, my = statistics.mean(xs), statistics.mean(ys)
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / max(sum((x - mx) ** 2 for x in xs), 1e-12)
    print(f"\nmk30 ~ VPIN_20 斜率 = {slope:+.4f} bp/单位VPIN"
          f"（负 = 高毒性⇒更差 markout ⇒ VPIN 门有信息量）")
    OUT.write_text(json.dumps({"hours": a.hours, "n": n, "slope_mk30_vs_vpin20": round(slope, 4),
                               "tiers": out}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已存 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
