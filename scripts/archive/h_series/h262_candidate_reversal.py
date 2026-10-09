# -*- coding: utf-8 -*-
"""H262 验证 3：候选池反转 corr（P1 选币的依据）。

# 背景

设计（全面升级 v2）P1 = 选币依据从"做市净额"改成"逐币反转 alpha"。
当前四币已测（H261）：XRP +4.03 > HYPE +3.62 > SOL +3.00 > ASTER +2.51bp。

但候选池（宽点差币，做市时代被"p25点差>4bp全负"淘汰）没测过。
它们在反转框架下可能更强：宽点差=大波动=大趋势=可能强反转（H257 幅度单调）。

# 本脚本

用 H255 的纯 tick 方法（不需要账本腿）：
  1s 网格、lookback=120s、fwd=60s（H255 最强的组合）、30s 去重叠
  逐币算 corr(past_120s, fwd_60s)，负 = 反转，正 = 动量

排序，看哪些候选的反转 corr 强于当前四币（参考值：SOL −0.0705 最强）。

# 判据

  · corr 显著为负（< −0.02）且样本足够 ⇒ 反转 alpha，可入宇宙
  · corr ≈ 0 或为正 ⇒ 无反转/动量，不入
  · 排除 *USD1（用户已放弃 USD1 市场）

# 用法

    python scripts/h262_candidate_reversal.py --hours 12
"""
from __future__ import annotations

import argparse
import json
import pathlib
import statistics as st
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "research_l1" / "out" / "h262_candidate_reversal.json"
K = 120.0
M = 60.0
STEP = 30.0


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
    if n < 30:
        return 0.0, 0.0
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    if sxx <= 0 or syy <= 0:
        return 0.0, 0.0
    r = sxy / (sxx * syy) ** 0.5
    t = r * ((n - 2) / (1 - r * r)) ** 0.5 if abs(r) < 1 else 0.0
    return r, t


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=12.0)
    a = ap.parse_args()

    import psycopg
    with psycopg.connect(market_dsn()) as c:
        with c.cursor() as cur:
            cur.execute("""
                SELECT symbol FROM asterdex_book_ticker
                WHERE ingest_ts >= now() - (%s || ' hours')::interval
                  AND bid_px>0 AND ask_px>bid_px
                GROUP BY 1 HAVING count(*) > 30000
            """, (str(float(a.hours) + 0.2),))
            cands = [r[0] for r in cur.fetchall()]

    print("=" * 104)
    print(f"H262  候选池反转 corr（lookback={K:g}s → fwd={M:g}s，负=反转）")
    print("=" * 104)
    print(f"\n  窗口 {a.hours:g}h　候选 {len(cands)} 个（tick>3万）")

    results = []
    for sym in cands:
        if sym.endswith("USD1"):
            continue
        try:
            with psycopg.connect(market_dsn()) as c:
                with c.cursor() as cur:
                    cur.execute("""
                        SELECT DISTINCT ON (bucket) bucket, mid
                        FROM (SELECT (event_ts_ms/1000) AS bucket,
                                     (bid_px+ask_px)/2.0 AS mid
                              FROM asterdex_book_ticker
                              WHERE ingest_ts >= now() - (%s || ' hours')::interval
                                AND symbol = %s AND bid_px>0 AND ask_px>bid_px) t
                        ORDER BY bucket, mid
                    """, (str(float(a.hours) + 0.2), sym))
                    recs = cur.fetchall()
        except Exception as e:
            print(f"  ⚠️ {sym}: {str(e)[:50]}")
            continue
        d = {int(b): float(mid) for b, mid in recs}
        ks = sorted(d)
        mids = [d[k] for k in ks]
        n = len(ks)
        # 去重叠采样起点
        starts, last = [], -1e18
        for i in range(n):
            if ks[i] - last >= STEP:
                starts.append(i)
                last = ks[i]
        xs, ys = [], []
        for i in starts:
            j = i
            while j >= 0 and ks[i] - ks[j] < K:
                j -= 1
            if j < 0 or ks[i] - ks[j] < K * 0.9 or mids[j] <= 0:
                continue
            f = i
            while f + 1 < n and ks[f + 1] - ks[i] < M:
                f += 1
            if f == i or ks[f] - ks[i] < M * 0.9 or mids[i] <= 0:
                continue
            xs.append((mids[i] - mids[j]) / mids[j] * 1e4)
            ys.append((mids[f] - mids[i]) / mids[i] * 1e4)
        r, t = pearson(xs, ys)
        results.append({"symbol": sym.replace("USDT", ""), "corr": round(r, 5),
                        "t": round(t, 2), "n": len(xs)})

    results.sort(key=lambda x: x["corr"])   # 最负 = 反转最强
    print(f"\n  {'symbol':<12}{'corr':>10}{'t值':>9}{'样本':>8}  判定")
    for r in results:
        verdict = "★ 强反转" if r["corr"] < -0.03 and r["n"] > 500 else (
            "✓ 反转" if r["corr"] < -0.015 and r["n"] > 500 else
            "~ 弱" if abs(r["corr"]) < 0.015 else
            "✗ 动量" if r["corr"] > 0.015 else "样本不足")
        cur = " ←当前" if r["symbol"] in ("ASTER", "SOL", "XRP", "HYPE") else ""
        print(f"  {r['symbol']:<12}{r['corr']:>+10.5f}{r['t']:>9.1f}{r['n']:>8}  {verdict}{cur}")

    # 结论：强于当前四币的候选
    cur_ref = {"SOL": -0.0705, "HYPE": -0.0145, "ASTER": -0.0147, "XRP": -0.0462}
    stronger = [r for r in results if r["corr"] < min(cur_ref.values()) and r["n"] > 500]
    print(f"\n{'━'*104}\n  结论\n{'━'*104}")
    print(f"\n  当前四币反转 corr 参考（H255）：SOL −0.0705 / XRP −0.0462 / "
          f"ASTER −0.0147 / HYPE −0.0145")
    if stronger:
        print(f"\n  **反转 corr 强于当前最弱（−0.0145）的候选**：")
        for r in stronger[:12]:
            print(f"     {r['symbol']:<12} corr {r['corr']:+.5f}  n={r['n']}")
    else:
        print(f"\n  ⇒ 没有候选的反转 corr 显著强于当前四币")
    print(f"\n  ⚠️ corr 只是线性信号；是否可交易还要看 |corr| 对应的条件期望幅度")
    print(f"     与成交率（H257 的逆势−顺势差才是最终判据）。")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"hours": a.hours, "k": K, "m": M,
                               "results": results}, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print(f"\n  写出 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
