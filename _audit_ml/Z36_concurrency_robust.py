# -*- coding: utf-8 -*-
"""Z36：并发上限的稳健性核验（顺序无关 + 留一法）。

Z35 的贪婪回放依赖「先到先得」的取舍顺序。本脚本做两项与顺序无关的核验：
A. 每笔开仓时的**真实并发数**（含仍持仓）→ 分档看结局（完全不依赖回放规则）；
B. 留一法：剔除 cap=3 被跳过的任一最差笔后，收益还剩多少（单事件依赖检查）。
"""
from __future__ import annotations

import os
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "_audit_ml"))

from Z35_concurrency_cap import agg, load, replay  # noqa: E402


def main() -> int:
    recs_all = load(75)
    recs = [r for r in recs_all if not r["is_open"]]
    print(f"已平仓 n={len(recs)}，仍持仓 {len(recs_all)-len(recs)} 笔（占名额）")

    # ---------- A. 顺序无关：开仓时的真实并发数 ----------
    print("\n=== A. 开仓时真实并发数 → 结局（不依赖任何回放规则）===")
    print(f"{'并发档':<12}{'n':>5}{'总USD':>10}{'均值%':>9}{'胜率':>7}{'模式率':>8}{'≤-2%':>7}")
    buckets = defaultdict(list)
    for r in recs:
        n_conc = sum(1 for o in recs_all
                     if o["id"] != r["id"] and o["open_ts"] < r["open_ts"] < o["close_ts"])
        r["conc"] = n_conc
        buckets[min(n_conc, 6)].append(r)
    labels = {0: "0（独占）", 1: "1", 2: "2", 3: "3", 4: "4", 5: "5", 6: "6+"}
    for k in sorted(buckets):
        a = agg(buckets[k])
        print(f"{labels[k]:<12}{a['n']:>5}{a['usd']:>+10.2f}{a['mean']:>+9.3f}"
              f"{a['win']:>7.3f}{a['pat']:>8.3f}{a['le2']:>7}")
    lo = [r for r in recs if r["conc"] <= 2]
    hi = [r for r in recs if r["conc"] >= 3]
    for label, sub in (("并发≤2（宽松）", lo), ("并发≥3（拥挤）", hi)):
        a = agg(sub)
        print(f"  {label:<16}{a['n']:>4}{a['usd']:>+10.2f} 均值={a['mean']:+.3f}% "
              f"模式率={a['pat']:.3f} ≤-2%={a['le2']}")

    print("\n=== A2. 逐月：并发≤2 vs ≥3（顺序无关，看是否只在某月成立）===")
    print(f"  {'月份':<10}{'≤2 n':>6}{'≤2 USD':>10}{'≤2 均值%':>10}{'≥3 n':>6}"
          f"{'≥3 USD':>10}{'≥3 均值%':>10}")
    for mon in sorted({r["mon"] for r in recs}):
        a = agg([r for r in lo if r["mon"] == mon])
        b = agg([r for r in hi if r["mon"] == mon])
        if not a or not b:
            continue
        print(f"  {mon:<10}{a['n']:>6}{a['usd']:>+10.2f}{a['mean']:>+10.3f}"
              f"{b['n']:>6}{b['usd']:>+10.2f}{b['mean']:>+10.3f}")

    print("\n=== A3. 并发≥3 里最差 5 笔（看是否单事件主导）===")
    for r in sorted(hi, key=lambda x: x["usd"])[:5]:
        print(f"    #{r['id']:<5}{r['symbol']:<9}{r['tier']:<6}{r['mon']} "
              f"USD={r['usd']:>+8.2f} 并发={r['conc']} 峰值={r['peak']:>5.2f}% {r['opened'][:16]}")
    top5 = sum(r["usd"] for r in sorted(hi, key=lambda x: x["usd"])[:5])
    print(f"    「并发≥3」合计 {agg(hi)['usd']:+.2f}，其中最差 5 笔 {top5:+.2f}"
          f" → 其余 {len(hi)-5} 笔 {agg(hi)['usd']-top5:+.2f}")

    # ---------- B. 留一法 ----------
    print("\n=== B. 留一法（cap=3）：剔除任一被跳过的笔后还剩多少收益 ===")
    keep, skip = replay(recs_all, 3)
    base_usd = sum(r["usd"] for r in keep)
    actual_usd = sum(r["usd"] for r in recs)
    print(f"  cap=3 保留 {len(keep)} 笔 USD={base_usd:+.2f}（实际 {actual_usd:+.2f}，"
          f"改善 {base_usd-actual_usd:+.2f}）")
    print(f"  被跳过 {len(skip)} 笔 USD={sum(r['usd'] for r in skip):+.2f}")
    print("  被跳过笔明细（按 USD 升序，前 12）：")
    for r in sorted(skip, key=lambda x: x["usd"])[:12]:
        print(f"    #{r['id']:<5}{r['symbol']:<9}{r['tier']:<6}{r['mon']} "
              f"USD={r['usd']:>+8.2f} 峰值={r['peak']:>5.2f}% 开于{r['opened'][:16]}")
    gains = []
    for r in skip:
        g = -(sum(x["usd"] for x in skip) - r["usd"])   # 剔除该笔后的「避免亏损」
        gains.append((g, r))
    gains.sort(key=lambda x: x[0])
    print(f"  留一法改善区间：最差 ${gains[0][0]:+.2f}（剔除 {gains[0][1]['symbol']} 那笔后）"
          f" ~ 最好 ${gains[-1][0]:+.2f}")
    neg = sum(1 for g, _ in gains if g <= 0)
    print(f"  剔除任一笔后改善转负的次数={neg}/{len(gains)} → "
          f"{'单事件驱动，不稳健' if neg > 0 else '较稳健'}")
    # 若只保留前 5 大亏损被跳过的情况
    top5 = sorted(skip, key=lambda x: x["usd"])[:5]
    print(f"  若只剔除最差 5 笔（合计 ${sum(r['usd'] for r in top5):+.2f}）："
          f"改善 ${-sum(r['usd'] for r in top5):+.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
