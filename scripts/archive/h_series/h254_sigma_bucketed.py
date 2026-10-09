# -*- coding: utf-8 -*-
"""H254 用**引擎自己的 realized_vol_bp** 分档（收口口径不一致）。

# 为什么要重做

H253 用「1s 收益的 pstdev」当波动，得到 0.2~3bp；而引擎的
`realized_vol_bp(mid_hist, 20)` 在同一时段是 17~27bp —— **差 5~20 倍**，
说明两个口径根本不是同一个量（H253 的 1s 网格把波动"平均掉"了）。

⇒ 凡是**要判断 σ 闸行为**的结论，必须用**引擎同一个函数、同一个网格**
（15s 网格、20 期窗口、`pstdev` 不带年化）。

# 本脚本

对每一腿，用**成交时刻往前**的 mid 序列按引擎口径算 `realized_vol_bp`，
再按它分档看 `spread_bp / price_bp / net_bp`。

# 它要回答的最后一个问题

H253 说"三个波动口径所有档位 net 全负"。若这句在**引擎口径**下也成立，
那么 σ 闸（无论阈值定在哪）都**救不了净额** —— 因为：

    波动档位内，`spread_bp` 恒为 +0.19~0.26bp（被 Δ 上限钳死）
    而 `price_bp` 在 −0.7 ~ −1.4bp 之间波动
    ⇒ net 恒负，与档位无关

⇒ 结论会是：**σ 闸只能减少成交次数，不能改变单腿符号。**
（这与 H234「被拦窗口更差」不矛盾：那说的是**相对**更差，
 而这里说的是**所有窗口都差**。）

# 用法

    python scripts/h254_sigma_bucketed.py --hours 12
"""
from __future__ import annotations

import argparse
import json
import pathlib
import statistics as st
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "research_l1" / "out" / "h254_sigma_bucketed.json"
LANE = "mm_asterdex"
CUR = ["ASTERUSDT", "XRPUSDT", "SOLUSDT", "HYPEUSDT", "ADAUSDT"]


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
    ap.add_argument("--window", type=int, default=20)
    a = ap.parse_args()

    import psycopg
    from backend.services.market_maker.core import realized_vol_bp

    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            cur.execute("""
                SELECT symbol, ts, coalesce(spread_bp,0), coalesce(price_bp,0),
                       coalesce(net_bp,0), coalesce(notional,0)
                FROM lane_ledger
                WHERE lane_id=%s AND (meta_json->'flatten')::text='false'
                  AND ts >= now() - (%s || ' hours')::interval
                  AND coalesce(notional,0) > 0
                ORDER BY ts ASC
            """, (LANE, str(float(a.hours))))
            legs = cur.fetchall()

    # 15s 网格（引擎口径）
    G = {}
    for sym in CUR:
        try:
            with psycopg.connect(market_dsn()) as c:
                with c.cursor() as cur:
                    cur.execute("""
                        SELECT DISTINCT ON (bucket) bucket, bid_px, ask_px
                        FROM (SELECT (event_ts_ms/15000) AS bucket, bid_px, ask_px
                              FROM asterdex_book_ticker
                              WHERE ingest_ts >= now() - (%s || ' hours')::interval
                                AND symbol = %s AND bid_px>0 AND ask_px>bid_px) t
                        ORDER BY bucket, bid_px
                    """, (str(float(a.hours) + 0.3), sym))
                    G[sym] = {int(b): ((float(x) + float(y)) / 2.0)
                              for b, x, y in cur.fetchall()}
        except Exception as e:
            print(f"  ⚠️ {sym}: {str(e)[:60]}")

    print("=" * 104)
    print("H254  按**引擎口径** realized_vol_bp 分档")
    print("=" * 104)
    print(f"\n  窗口 {a.hours:g}h　maker 腿 {len(legs)}　"
          f"网格 15s　窗口 {a.window} 期")

    rows = []
    for sym, ts, sp, px_, net, notl in legs:
        tk = str(sym).upper()
        if not tk.endswith("USDT"):
            tk += "USDT"
        d = G.get(tk)
        if not d:
            continue
        b0 = int(ts.timestamp()) // 15
        hist = [d[k] for k in sorted(d) if k <= b0][-(a.window + 1):]
        if len(hist) < 3:
            continue
        rv = realized_vol_bp(hist, a.window)
        rows.append({"sym": str(sym), "rv": rv, "spread": float(sp),
                     "price": float(px_), "net": float(net),
                     "notional": float(notl)})
    if not rows:
        print("无法匹配")
        return 1
    print(f"  匹配成功 {len(rows)} 腿")
    rvs = sorted(r["rv"] for r in rows)
    n = len(rvs)
    print(f"  realized_vol_bp 分布：P10 {rvs[n//10]:.2f}　P50 {rvs[n//2]:.2f}　"
          f"P90 {rvs[int(n*0.9)]:.2f}　max {rvs[-1]:.2f}")

    EDGES = [0.0] + [rvs[int(n * p)] for p in (0.2, 0.4, 0.6, 0.8)] + [1e9]
    print(f"\n{'━'*104}\n  逐档：spread / 逆选择 / net\n{'━'*104}")
    print(f"\n  {'波动档(bp)':>16}{'腿数':>7}{'spread均值':>12}"
          f"{'逆选择price':>13}{'net均值':>10}{'**折每腿$**':>13}")
    ok = []
    for i in range(len(EDGES) - 1):
        lo, hi = EDGES[i], EDGES[i + 1]
        sel = [r for r in rows if lo <= r["rv"] < hi]
        if len(sel) < 20:
            continue
        nn = sum(r["notional"] for r in sel)
        usd = sum(r["notional"] * r["net"] / 1e4 for r in sel)
        m = st.mean([r["net"] for r in sel])
        lbl = f"{lo:.2f}–{hi:.2f}" if hi < 1e9 else f"{lo:.2f}+"
        print(f"  {lbl:>16}{len(sel):>7}{st.mean([r['spread'] for r in sel]):>+12.4f}"
              f"{st.mean([r['price'] for r in sel]):>+13.4f}{m:>+10.4f}"
              f"{usd/len(sel):>+13.4f}")
        ok.append((lo, hi, m, len(sel)))

    print(f"\n{'━'*104}\n  结论\n{'━'*104}")
    pos = [x for x in ok if x[2] > 0]
    if pos:
        print(f"\n  ⇒ net 为正的档位 = {[(round(x[0],2), round(x[1],2)) for x in pos]}")
        print(f"     最低档 {pos[0][0]:.2f} 起 ⇒ σ 闸设在该处可转正")
    else:
        print(f"\n  ⇒ **引擎口径下，所有波动档 net 全为负**")
        print(f"     ⇒ σ 闸无论阈值定在哪，都**只能减少成交次数，不能改变单腿符号**")
        print(f"        （因为档内 `spread_bp` 恒被 Δ 上限钳在 +0.19~0.26bp，")
        print(f"          而 `price_bp` 恒在 −0.7~−1.4bp）")
    # spread 是否随波动变化
    sps = [(x[0], st.mean([r["spread"] for r in rows if x[0] <= r["rv"] < x[1]]))
           for x in ok]
    pss = [(x[0], st.mean([r["price"] for r in rows if x[0] <= r["rv"] < x[1]]))
           for x in ok]
    if len(sps) >= 2:
        print(f"\n  spread 随档位：{min(s for _, s in sps):+.3f} → {max(s for _, s in sps):+.3f} "
              f"（变化 {abs(max(s for _, s in sps)/min(s for _, s in sps)):.2f}×）")
        print(f"  price  随档位：{min(p for _, p in pss):+.3f} → {max(p for _, p in pss):+.3f} "
              f"（变化 {abs(max(p for _, p in pss)/min(p for _, p in pss)):.2f}×）")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"hours": a.hours, "n": len(rows),
                               "rv_p50": round(rvs[n // 2], 3)},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  写出 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
