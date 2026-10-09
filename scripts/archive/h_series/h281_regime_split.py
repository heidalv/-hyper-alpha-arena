# -*- coding: utf-8 -*-
"""H281 regime 条件化：反转 corr 在什么条件下失效（脆弱性量化）。

# 问题（用户：脆弱）

  同一条"120s 涨→卖"规则，夜间连赚 3 小时、早晨趋势段挨打。
  本脚本把反转可预测性按三个维度切片，找出失效条件：
    R1 波动率分位（过去 300s 已实现波动，中位数切）
    R2 时段（北京 00-08 夜盘 vs 08-24 日盘）
    R3 趋势持续度（|过去 300s 净移动|：<5bp / 5~20bp / ≥20bp）
  另附：fwd 均值随 |past 120s 趋势| 分桶（验证 H257 单调性在 48h 数据上是否还成立）。

# 口径：1s 网格、60s 去重叠、48h、当前 4 币；n<300 标记不足。

# 用法

    python scripts/h281_regime_split.py --hours 48
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from datetime import datetime, timezone, timedelta

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "research_l1" / "out" / "h281_regime_split.json"
CUR = ["SOLUSDT", "DOGEUSDT", "ETHUSDT", "BNBUSDT"]
BJT = timezone(timedelta(hours=8))


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
        return 0.0, 0.0, 0
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    if sxx <= 0 or syy <= 0:
        return 0.0, 0.0, n
    r = sxy / (sxx * syy) ** 0.5
    t = r * ((n - 2) / (1 - r * r)) ** 0.5 if abs(r) < 1 else (float("inf") if r > 0 else float("-inf"))
    return r, t, n


def bucket_stat(name, xs, ys):
    if len(xs) < 300:
        return {"bucket": name, "n": len(xs), "corr": None, "t": None, "fwd_mean_bp": None,
                "note": "n<300 不足"}
    r, t, n = pearson(xs, ys)
    return {"bucket": name, "n": n, "corr": round(r, 5), "t": round(t, 2),
            "fwd_mean_bp": round(sum(ys) / n, 4)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=48.0)
    a = ap.parse_args()

    import psycopg

    series = {}
    for sym in CUR:
        with psycopg.connect(market_dsn()) as c:
            with c.cursor() as cur:
                cur.execute("""
                    SELECT DISTINCT ON (bucket) bucket, bid_px, ask_px
                    FROM (SELECT (event_ts_ms/1000) AS bucket, bid_px, ask_px
                          FROM asterdex_book_ticker
                          WHERE event_ts_ms >= (extract(epoch from now())*1000 - %s*3600*1000)::bigint
                            AND symbol = %s AND bid_px>0 AND ask_px>bid_px) t
                    ORDER BY bucket, bid_px
                """, (a.hours, sym))
                recs = cur.fetchall()
        d = {int(b): ((float(x) + float(y)) / 2.0) for b, x, y in recs}
        ks_sorted = sorted(d)
        series[sym] = (ks_sorted, [d[k] for k in ks_sorted])
        print(f"  {sym:10} {len(ks_sorted):>8} 点", flush=True)

    # 采样：60s 去重叠；对每个事件算 past120、fwd60、past300（用于条件）
    K, M = 120.0, 60.0
    events = []  # (ts, symbol, past120_bp, fwd60_bp, vol300_bp, |trend300|, hour)
    for sym, (ks_sorted, px) in series.items():
        n = len(ks_sorted)
        last = -1e18
        for i in range(n):
            if ks_sorted[i] - last < 60.0:
                continue
            last = ks_sorted[i]
            # past120
            j = i
            while j >= 0 and ks_sorted[i] - ks_sorted[j] < K:
                j -= 1
            if j < 0 or ks_sorted[i] - ks_sorted[j] < K * 0.9 or px[j] <= 0:
                continue
            # fwd60
            f = i
            while f + 1 < n and ks_sorted[f + 1] - ks_sorted[i] < M:
                f += 1
            if f == i or ks_sorted[f] - ks_sorted[i] < M * 0.9 or px[i] <= 0:
                continue
            # vol300：过去 300s 每秒 |r| 和（bps）
            vj = i
            while vj >= 0 and ks_sorted[i] - ks_sorted[vj] < 300.0:
                vj -= 1
            vol = 0.0
            if vj >= 0 and i - vj > 10:
                seg = px[vj:i + 1]
                vol = sum(abs((seg[t + 1] - seg[t]) / seg[t]) * 1e4 for t in range(len(seg) - 1))
            past300 = (px[i] - px[vj]) / px[vj] * 1e4 if vj >= 0 and px[vj] > 0 else 0.0
            past = (px[i] - px[j]) / px[j] * 1e4
            fwd = (px[f] - px[i]) / px[i] * 1e4
            hour = datetime.fromtimestamp(ks_sorted[i], tz=BJT).hour
            events.append((ks_sorted[i], sym, past, fwd, vol, abs(past300), hour))

    print(f"\n  事件总数（去重叠）: {len(events)}", flush=True)

    # 全样本基线
    xs_all = [e[2] for e in events]
    ys_all = [e[3] for e in events]
    r0, t0, n0 = pearson(xs_all, ys_all)
    print(f"\n  基线（全样本）: corr={r0:.4f}  t={t0:.1f}  n={n0}  fwd_mean={sum(ys_all)/n0:+.3f}bp")

    out = {"baseline": {"corr": round(r0, 5), "t": round(t0, 2), "n": n0}, "splits": {}}

    # R1 波动率中位切
    vols = sorted(e[4] for e in events)
    med_vol = vols[len(vols) // 2]
    for name, cond in (("低波动", lambda e: e[4] <= med_vol), ("高波动", lambda e: e[4] > med_vol)):
        sub = [e for e in events if cond(e)]
        s = bucket_stat(f"R1 {name}(vol≤/{'>'}med={med_vol:.1f}bp)", [e[2] for e in sub], [e[3] for e in sub])
        out["splits"][s["bucket"]] = s
        print(f"  {s['bucket']:44s} n={s['n']:6d} corr={s.get('corr')}  fwd={s.get('fwd_mean_bp')}")

    # R2 时段
    for name, cond in (("夜盘 00-08", lambda e: e[6] < 8), ("日盘 08-24", lambda e: e[6] >= 8)):
        sub = [e for e in events if cond(e)]
        s = bucket_stat(f"R2 {name}", [e[2] for e in sub], [e[3] for e in sub])
        out["splits"][s["bucket"]] = s
        print(f"  {s['bucket']:44s} n={s['n']:6d} corr={s.get('corr')}  fwd={s.get('fwd_mean_bp')}")

    # R3 趋势持续度
    for name, lo, hi in (("小趋势 <5bp", 0, 5), ("中趋势 5-20bp", 5, 20), ("大趋势 ≥20bp", 20, 1e9)):
        sub = [e for e in events if lo <= e[5] < hi]
        s = bucket_stat(f"R3 {name}", [e[2] for e in sub], [e[3] for e in sub])
        out["splits"][s["bucket"]] = s
        print(f"  {s['bucket']:44s} n={s['n']:6d} corr={s.get('corr')}  fwd={s.get('fwd_mean_bp')}")

    # |past120| 分桶 → fwd 均值（单调性验证）
    print("\n  |past120s| 分桶 → fwd60 均值（H257 单调性复查）:")
    buckets = [(0, 1), (1, 2), (2, 3), (3, 5), (5, 10), (10, 20), (20, 1e9)]
    out["trend_buckets"] = []
    for lo, hi in buckets:
        sub = [e for e in events if lo <= abs(e[2]) < hi]
        if not sub:
            continue
        fwd_mean = sum(e[3] for e in sub) / len(sub)
        r, t, n = pearson([e[2] for e in sub], [e[3] for e in sub])
        rec = {"bucket": f"{lo}~{hi if hi < 1e9 else '∞'}bp", "n": n,
               "fwd_mean_bp": round(fwd_mean, 4), "corr": round(r, 5), "t": round(t, 2)}
        out["trend_buckets"].append(rec)
        print(f"    {rec['bucket']:12s} n={n:6d} fwd_mean={fwd_mean:+.3f}bp  corr={r:.4f}  t={t:.1f}")

    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  已存 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
