# -*- coding: utf-8 -*-
"""H331 对手盘分析：成交我们的对手方是谁、他们的交易有多大、成交时流环境如何。

# 目的（根基研究·对手盘数据层）
   被动做市亏的钱 = 对手方的赚的钱。对每一笔**我们的成交**测量：
   A. 成交腿量（qty）三分位 × 后续 markout —— 大单成交 = 知情对手？（逆选择主通道）
   B. 成交时 OFI 桶（15s 主动买卖失衡）三分位 × markout —— 有毒流环境？
   C. 成交后 5s 内市场的主动交易量与方向（asterdex_trades 1s VWAP）—— 对手是否在扫单
   全部按方向带符号（markout 正=对我们有利）。

# 用法: python scripts/h331_opponent_flow.py [--hours 48]
"""
from __future__ import annotations

import argparse
import bisect
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "research_l1" / "out" / "h331_opponent.json"


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

    # 1) 我们的成交
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

    # 2) 每币 1s 中价 + OFI(15s) + 1s 成交流
    syms = sorted({f[0] for f in fills})
    series, ofi, flows = {}, {}, {}
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
                o = {}
                for ts_ms, bn, sn in cur.fetchall():
                    tot = float(bn) + float(sn)
                    if tot > 0:
                        o[int(ts_ms) // 15000] = (float(bn) - float(sn)) / tot
                ofi[s] = o
                cur.execute("""
                    SELECT (event_ts_ms/1000)::bigint AS b, sum(price*qty), sum(qty)
                    FROM asterdex_trades
                    WHERE symbol=%s AND event_ts_ms >= (extract(epoch from now())*1000 - %s*3600*1000)::bigint
                    GROUP BY b ORDER BY b
                """, (bs, a.hours + 1))
                flows[s] = cur.fetchall()

    def mid_at(s, t):
        ks, ms = series[s]
        j = bisect.bisect_right(ks, t) - 1
        if j < 0 or t - ks[j] > 5:
            return None
        return ms[j]

    def flow_5s(s, t):
        """成交后 5s 内市场主动交易的净主动买（按 VWAP 符号近似：价高于成交前 mid 记买）。"""
        rows = flows[s]
        j = bisect.bisect_right([r[0] for r in rows], t) - 1
        buy_v = sell_v = 0.0
        m0 = mid_at(s, t)
        for b, vnum, qty in rows[j + 1:]:
            if b - t > 5:
                break
            vwap = vnum / qty if qty else 0.0
            if m0 and vwap > 0:
                if vwap >= m0:
                    buy_v += float(vnum)
                else:
                    sell_v += float(vnum)
        tot = buy_v + sell_v
        return (buy_v - sell_v) / tot if tot > 0 else 0.0

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
        recs.append({
            "sym": sym, "notional": abs((qty or 0.0) * px),
            "mk30": (m30 - px) / mid0 * 1e4 * sign,
            "mk300": (m300 - px) / mid0 * 1e4 * sign,
            "ofi": ofi[sym].get(t0 // 15, 0.0),
            "flow5": flow_5s(sym, t0),
        })
    n = len(recs)
    print(f"可用样本 {n} 笔\n")

    def terciles(key):
        xs = sorted(r[key] for r in recs)
        return xs[n // 3], xs[2 * n // 3]

    def wmean(sub, key):
        tot = sum(r["notional"] for r in sub) or 1.0
        return sum(r[key] * r["notional"] for r in sub) / tot

    t1, t2 = terciles("notional")
    print("A. 成交腿量三分位 × markout（大单 = 知情对手？）")
    print(f"  {'分位':<14} {'n':>5} {'mk30':>9} {'mk300':>10}")
    for lab, lo, hi in (("小(<%.0f$)" % t1, -1e18, t1), ("中", t1, t2), ("大(>%.0f$)" % t2, t2, 1e18)):
        sub = [r for r in recs if lo <= r["notional"] < hi]
        if sub:
            print(f"  {lab:<14} {len(sub):>5} {wmean(sub,'mk30'):>+9.3f} {wmean(sub,'mk300'):>+10.3f}")

    o1, o2 = terciles("ofi")
    print("\nB. 成交时 OFI 桶三分位 × markout（有毒流环境？）")
    print(f"  {'OFI 分位':<14} {'n':>5} {'mk30':>9} {'mk300':>10}")
    for lab, lo, hi in (("负(<%+.2f)" % o1, -1e18, o1), ("中", o1, o2), ("正(>%+.2f)" % o2, o2, 1e18)):
        sub = [r for r in recs if lo <= r["ofi"] < hi]
        if sub:
            print(f"  {lab:<14} {len(sub):>5} {wmean(sub,'mk30'):>+9.3f} {wmean(sub,'mk300'):>+10.3f}")

    f1, f2 = terciles("flow5")
    print("\nC. 成交后 5s 市场主动流 × markout（对手扫单？）")
    print(f"  {'flow5 分位':<14} {'n':>5} {'mk30':>9} {'mk300':>10}")
    for lab, lo, hi in (("负(<%+.2f)" % f1, -1e18, f1), ("中", f1, f2), ("正(>%+.2f)" % f2, f2, 1e18)):
        sub = [r for r in recs if lo <= r["flow5"] < hi]
        if sub:
            print(f"  {lab:<14} {len(sub):>5} {wmean(sub,'mk30'):>+9.3f} {wmean(sub,'mk300'):>+10.3f}")

    print(f"\n总体：mk30 = {wmean(recs,'mk30'):+.3f}bp   mk300 = {wmean(recs,'mk300'):+.3f}bp"
          f"（= 对手方从我们身上平均拿走的毛利润）")
    OUT.write_text(json.dumps({"hours": a.hours, "n": n, "all_mk30": wmean(recs, "mk30"),
                               "all_mk300": wmean(recs, "mk300")},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已存 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
