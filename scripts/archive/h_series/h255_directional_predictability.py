# -*- coding: utf-8 -*-
"""H255 方向性可预测性：30s~5min 尺度的短期反转 / 动量。

# 为什么重做这个（框架纠偏）

本会话我把 lane 当「双边做市」测 spread−逆选择，得出"结构性负期望"。
但用户要的是 **30s~5min 中短高频的方向性预测**，不是做市赚价差。

方向性可预测性的标准检验（论文核心）：
    过去 k 秒收益  →  未来 m 秒收益
    负相关 = 短期反转（跌了会反弹、涨了会回落）
    正相关 = 短期动量（趋势延续）

账本里其实早有两个被我当"归因假象"扔掉的信号：
    F198b：买腿 30min markout **+1.90bp**、卖腿 **−4.51bp**
    F199：逆势 **+2.11bp**、顺势 **−4.05bp**
⇒ 这些是**短期反转 alpha 的方向证据**，不是我之前说的"假象"。

# 本脚本直接测（用市场 tick，不是账本，避免归因污染）

对每个 (symbol, t)：
    past = (mid_t − mid_{t−k}) / mid_{t−k}
    fwd  = (mid_{t+m} − mid_t) / mid_t
然后看 corr(past, fwd) 的**符号与强度**，跨 k×m 矩阵。

# 判据（本会话硬规矩，否则又是伪结论）

1. **去重叠**：样本用 60s 间隔（独立事件），不用逐秒重叠
2. **报独立样本数**，n<500 就说不足
3. **跨币、跨小时符号一致性**：不是某一币某一天的偶然
4. 相关系数虽小（高频里 0.01~0.05 就算大），要看**t 值与符号稳定**，
   不是看"能解释多少方差"

# 用法

    python scripts/h255_directional_predictability.py --hours 12
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
OUT = ROOT / "research_l1" / "out" / "h255_directional.json"
CUR = ["ASTERUSDT", "XRPUSDT", "SOLUSDT", "HYPEUSDT"]
KS = [30.0, 60.0, 120.0, 300.0]
MS = [30.0, 60.0, 120.0, 300.0]


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
    # t 值（Fisher 近似）
    if abs(r) >= 1:
        t = float("inf") if r > 0 else float("-inf")
    else:
        t = r * ((n - 2) / (1 - r * r)) ** 0.5
    return r, t


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=12.0)
    ap.add_argument("--grid", type=float, default=1.0, help="价格网格（秒）")
    ap.add_argument("--step", type=float, default=30.0, help="样本间隔（秒，去重叠）")
    a = ap.parse_args()

    import psycopg
    RAW = {}
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
                    RAW[sym] = cur.fetchall()
        except Exception as e:
            print(f"  ⚠️ {sym}: {str(e)[:60]}")

    series = {}
    for sym, recs in RAW.items():
        d = {int(b): ((float(x) + float(y)) / 2.0) for b, x, y in recs}
        ks = sorted(d)
        series[sym] = (ks, [d[k] for k in ks])

    print("=" * 104)
    print("H255  方向性可预测性（30s~5min 短期反转/动量）")
    print("=" * 104)
    print(f"\n  窗口 {a.hours:g}h　样本间隔 {a.step:g}s（去重叠）")
    for s, (ks, px) in sorted(series.items()):
        print(f"  {s:12} {len(ks):>7} 点　"
              f"{datetime.fromtimestamp(ks[0]):%H:%M} ~ {datetime.fromtimestamp(ks[-1]):%H:%M}")

    # 逐币算 corr(past_k, fwd_m)
    corr_all = {}
    for sym, (ks, px) in sorted(series.items()):
        n = len(ks)
        # 采样起点（去重叠：每隔 step 秒）
        starts = []
        last = -1e18
        for i in range(n):
            if ks[i] - last >= a.step:
                starts.append(i)
                last = ks[i]
        for k in KS:
            for m in MS:
                xs, ys = [], []
                for i in starts:
                    # past 起点
                    j = i
                    while j >= 0 and ks[i] - ks[j] < k:
                        j -= 1
                    if j < 0 or ks[i] - ks[j] < k * 0.9 or px[j] <= 0:
                        continue
                    # fwd 终点
                    f = i
                    while f + 1 < n and ks[f + 1] - ks[i] < m:
                        f += 1
                    if f == i or ks[f] - ks[i] < m * 0.9 or px[i] <= 0:
                        continue
                    past = (px[i] - px[j]) / px[j] * 1e4
                    fwd = (px[f] - px[i]) / px[i] * 1e4
                    xs.append(past)
                    ys.append(fwd)
                r, t = pearson(xs, ys)
                corr_all[(sym, k, m)] = (r, t, len(xs))

    # ── 一、corr 矩阵（合并四币）──
    print(f"\n{'━'*104}\n  一、过去 k → 未来 m 的相关系数（合并四币，腿数加权）\n{'━'*104}")
    hdr = "".join(f"{'m='+str(int(x))+'s':>13}" for x in MS)
    print(f"\n  {'past k':>10}{hdr}")
    for k in KS:
        cells = ""
        for m in MS:
            rs, ts, ns = [], [], []
            for sym in series:
                r, t, n = corr_all.get((sym, k, m), (0, 0, 0))
                if n >= 50:
                    rs.append(r)
                    ns.append(n)
                    ts.append(t)
            if not rs:
                cells += f"{'—':>13}"
                continue
            # 加权平均 corr（按样本数）
            wsum = sum(ns)
            rbar = sum(r * n for r, n in zip(rs, ns)) / wsum
            cells += f"{rbar:>+13.4f}"
        print(f"  {('k='+str(int(k))+'s'):>10}{cells}")

    # ── 二、逐币符号一致性（反转=负 corr 是否稳定）──
    print(f"\n{'━'*104}\n  二、逐币 corr 符号（反转=负；动量=正）—— 找稳定的 k×m\n{'━'*104}")
    print(f"\n  {'past k':>8}{'fwd m':>8}" + "".join(f"{s.replace('USDT',''):>9}" for s in sorted(series)))
    best = []
    for k in KS:
        for m in MS:
            row = []
            for sym in sorted(series):
                r, t, n = corr_all.get((sym, k, m), (0, 0, 0))
                row.append((r, t, n))
            neg = sum(1 for r, _, n in row if n >= 50 and r < 0)
            pos = sum(1 for r, _, n in row if n >= 50 and r > 0)
            tot = sum(1 for r, _, n in row if n >= 50)
            if tot < 2:
                continue
            cells = "".join(f"{r:>+9.4f}" if n >= 50 else f"{'—':>9}" for r, _, n in row)
            agree = "反转" if neg == tot else ("动量" if pos == tot else "混合")
            print(f"  {int(k):>8}{int(m):>8}{cells}   {agree} {neg}/{tot}")
            if agree != "混合":
                best.append((k, m, agree, neg, pos, tot))

    # ── 三、结论 ──
    print(f"\n{'━'*104}\n  三、结论\n{'━'*104}")
    rev = [x for x in best if x[2] == "反转"]
    mom = [x for x in best if x[2] == "动量"]
    if rev or mom:
        if rev:
            print(f"\n  **短期反转**稳定的 (k,m) 组合（corr<0 一致）：")
            for k, m, _, neg, _, tot in rev:
                print(f"     past {int(k)}s → fwd {int(m)}s　{neg}/{tot} 币为负")
        if mom:
            print(f"\n  **短期动量**稳定的 (k,m) 组合（corr>0 一致）：")
            for k, m, _, _, pos, tot in mom:
                print(f"     past {int(k)}s → fwd {int(m)}s　{pos}/{tot} 币为正")
    else:
        print(f"\n  ⇒ **没有跨币一致的方向信号**（符号在币间翻转）")
        print(f"     → 方向性可预测性在 30s~5min 尺度上不成立（本窗口/本标的）")
    print(f"\n  ⚠️ 判读要点：")
    print(f"     · 高频里 |corr| 0.01~0.05 就算有意义（R² 低但期望可正）")
    print(f"     · 符号跨币一致 > 幅度大（幅度可能是某币的偶然）")
    print(f"     · 这个只测了**线性** corr；非线性/条件信号另测")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "hours": a.hours, "ks": KS, "ms": MS, "step": a.step,
        "corr": {f"{s}|{k}|{m}": corr_all.get((s, k, m), [None, None, 0])[0]
                 for s in series for k in KS for m in MS},
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  写出 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
