# -*- coding: utf-8 -*-
"""H314 盘口深度研究：失衡因子 IC + 稀薄度 regime 门 + 深度-波动关系。

# 用户指令：现在有对盘口深度的分析吗？→ 没有，本脚本是第一个。
# 数据：asterdex_depth_snapshots（20 档 [price, qty]）+ book_ticker 1s mid。
# 问题：
    1. 深度失衡（前5档 买额−卖额/总额）能预测 60s/180s/300s 方向吗？（文献说弱，实测）
    2. 深度稀薄（前5档美元额 < 滚动中位数）时段，反转 corr 是否变差？（流动性蒸发门）
    3. 深度稀薄 vs 未来波动（|fwd60|）——薄盘口是否放大波动（逆向选择风险代理）
# 口径：7 天、车道 3 币（DOGE/ETH/BNB）、事件 60s 去重叠。

# 用法

    python scripts/h314_depth_analysis.py --days 7
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "research_l1" / "out" / "h314_depth_analysis.json"
CUR = ["DOGE", "ETH", "BNB"]


def market_dsn() -> str:
    env = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    base = env["DATABASE_URL"]
    for j in ("+psycopg2", "+psycopg", "+asyncpg"):
        base = base.replace(j, "")
    return base.rsplit("/", 1)[0] + "/alpha_market"


def pearson(xs, ys):
    n = len(xs)
    if n < 3:
        return 0.0, 0.0
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    if sxx <= 0 or syy <= 0:
        return 0.0, 0.0
    r = sxy / (sxx * syy) ** 0.5
    t = r * ((n - 2) / (1 - r * r)) ** 0.5 if abs(r) < 1 else float("inf")
    return r, t


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=float, default=7.0)
    a = ap.parse_args()

    import psycopg
    import bisect

    rows = []
    for sym in CUR:
        # 1s mid
        with psycopg.connect(market_dsn()) as c:
            with c.cursor() as cur:
                cur.execute("""
                    SELECT DISTINCT ON (bucket) bucket, bid_px, ask_px
                    FROM (SELECT (event_ts_ms/1000) AS bucket, bid_px, ask_px
                          FROM asterdex_book_ticker
                          WHERE event_ts_ms >= (extract(epoch from now())*1000 - %s*86400*1000)::bigint
                            AND symbol = %s AND bid_px>0 AND ask_px>bid_px) t
                    ORDER BY bucket, bid_px
                """, (a.days, sym + "USDT"))
                recs = cur.fetchall()
        d = {int(b): ((float(x) + float(y)) / 2.0) for b, x, y in recs}
        ks = sorted(d)
        mids = [d[k] for k in ks]
        # 深度快照（每 ~1s）：(ts_ms, top5_bid_usd, top5_ask_usd)
        # [h314b] 聚合放 SQL 侧（只传 3 列），autocommit 防长事务被 LeakGuard 杀
        with psycopg.connect(market_dsn(), autocommit=True) as c:
            with c.cursor() as cur:
                cur.execute("""
                    SELECT event_ts_ms,
                      (SELECT COALESCE(sum((l->>0)::float8 * (l->>1)::float8), 0)
                         FROM jsonb_array_elements(bids) WITH ORDINALITY t(l, o)
                        WHERE o <= 5) AS b5,
                      (SELECT COALESCE(sum((l->>0)::float8 * (l->>1)::float8), 0)
                         FROM jsonb_array_elements(asks) WITH ORDINALITY t(l, o)
                        WHERE o <= 5) AS a5
                    FROM asterdex_depth_snapshots
                    WHERE event_ts_ms >= (extract(epoch from now())*1000 - %s*86400*1000)::bigint
                      AND symbol = %s ORDER BY event_ts_ms
                """, (a.days, sym + "USDT"))
                deps = cur.fetchall()
        dts, ddep = [], []
        for ts_ms, b5, a5 in deps:
            dts.append(int(ts_ms))
            ddep.append((float(b5 or 0.0), float(a5 or 0.0)))
        print(f"  {sym:6} mid {len(ks)} 点  深度快照 {len(dts)}", flush=True)

        last = -1e18
        for i in range(len(ks)):
            if ks[i] - last < 60.0:
                continue
            last = ks[i]
            j = i
            while j >= 0 and ks[i] - ks[j] < 60.0:
                j -= 1
            if j < 0 or ks[i] - ks[j] < 54 or mids[j] <= 0:
                continue
            r60 = (mids[i] - mids[j]) / mids[j] * 1e4
            # fwd
            f1 = i + 60 if i + 60 < len(ks) else len(ks) - 1
            f3 = i + 180 if i + 180 < len(ks) else len(ks) - 1
            f5 = i + 300 if i + 300 < len(ks) else len(ks) - 1
            if mids[f1] <= 0 or mids[f3] <= 0 or mids[f5] <= 0:
                continue
            y1 = (mids[f1] - mids[i]) / mids[i] * 1e4
            y3 = (mids[f3] - mids[i]) / mids[i] * 1e4
            y5 = (mids[f5] - mids[i]) / mids[i] * 1e4
            # 对齐深度（最近一条 ≤ 当前时刻）
            k = bisect.bisect_right(dts, ks[i] * 1000) - 1
            if k < 0 or k >= len(dts):
                continue
            b5, a5 = ddep[k]
            tot = b5 + a5
            imb5 = (b5 - a5) / tot if tot > 0 else 0.0
            rows.append({"sym": sym, "r60": r60, "y1": y1, "y3": y3, "y5": y5,
                         "imb5": imb5, "dep5_usd": tot, "ts": ks[i]})

    print(f"\n  样本: {len(rows)}")
    # 1) 深度失衡 IC
    print("\n  ① 深度失衡 imb5 IC:")
    for yk, name in (("y1", "60s"), ("y3", "180s"), ("y5", "300s")):
        r, t = pearson([x["imb5"] for x in rows], [x[yk] for x in rows])
        print(f"    fwd {name}: IC {r:+.5f}  t {t:+.1f}")

    # 2) 稀薄度 → 反转 corr 条件化
    import statistics as st
    dep5 = sorted(x["dep5_usd"] for x in rows)
    t1 = dep5[len(dep5) // 3]
    t2 = dep5[2 * len(dep5) // 3]
    print(f"\n  ② 深度稀薄度 regime（三分位 {t1:.0f}/{t2:.0f} USD）→ 反转 corr(r60→fwd60):")
    out = {"n": len(rows), "imb_ic": {}, "thinness": {}}
    for name, cond in (("厚", lambda x: x["dep5_usd"] > t2),
                       ("中", lambda x: t1 <= x["dep5_usd"] <= t2),
                       ("薄", lambda x: x["dep5_usd"] < t1)):
        sub = [x for x in rows if cond(x)]
        r, t = pearson([x["r60"] for x in sub], [x["y1"] for x in sub])
        print(f"    {name}（n={len(sub)}）: corr {r:+.5f}  t {t:+.1f}")
        out["thinness"][name] = {"n": len(sub), "corr": round(r, 5), "t": round(t, 2)}

    # 3) 稀薄度 → 未来波动
    print("\n  ③ 深度稀薄 vs 未来 60s |波动|（bp）:")
    for name, cond in (("厚", lambda x: x["dep5_usd"] > t2),
                       ("中", lambda x: t1 <= x["dep5_usd"] <= t2),
                       ("薄", lambda x: x["dep5_usd"] < t1)):
        sub = [x for x in rows if cond(x)]
        v = sum(abs(x["y1"]) for x in sub) / len(sub)
        print(f"    {name}: 平均 |fwd60| = {v:.2f}bp")
        out["thinness"][name]["vol60_mean"] = round(v, 3)
    for yk, name in (("y1", "60s"), ("y3", "180s"), ("y5", "300s")):
        r, t = pearson([x["imb5"] for x in rows], [x[yk] for x in rows])
        out["imb_ic"][name] = {"ic": round(r, 5), "t": round(t, 2)}
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  已存 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
