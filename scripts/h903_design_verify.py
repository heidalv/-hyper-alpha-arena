# -*- coding: utf-8 -*-
"""[h903] 验证:h902 的发现 → 新设计 vs 现状的回测。

新设计(基于 h902 的实证):
  · 只做"挂单进场 + 15~45s 持有 + 挂单离场"的往返
  · 时段筛选:排除夜间 0-5(edge 最负)
  · 概率层改为"经验桶 edge"(按 币×时段 分桶),凯利式定仓:edge>0 才做
回测口径:近 14 天真实往返,按"如果只按新设计的条件做"重算净 bp 与复利。
"""
import io
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena")
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace", line_buffering=True)

rows = []
for line in (ROOT / "data" / "flow_roundtrip_log.jsonl").read_text(
        encoding="utf-8").splitlines():
    try:
        r = json.loads(line)
    except Exception:
        continue
    if r.get("y_bp") is None:
        continue
    rows.append(r)

now = time.time()
rows = [r for r in rows if float(r.get("ts") or 0) > now - 14 * 86400]


def is_maker_entry(r):
    return float(r.get("fee_bp") or 0) == 0


def hold(r):
    return float(r.get("hold_sec") or 0)


def hour(r):
    return time.localtime(float(r.get("ts") or 0)).tm_hour


print("=" * 70)
print("现状(全部往返)vs 新设计(15-45s 挂单往返 + 时段筛选)回测")
print("=" * 70)


def summary(rs, label):
    ys = np.array([float(r["y_bp"]) for r in rs])
    n = len(ys)
    m = float(ys.mean()) if n else 0.0
    t = float(m / (ys.std(ddof=1) / np.sqrt(n))) if n > 1 and ys.std(ddof=1) > 0 else 0.0
    eq = 1000.0
    for y in ys:
        eq *= (1.0 + 0.05 * y / 100.0)
    print(f"  {label:<26} n={n:>4} mean={m:+7.2f}bp t={t:+5.2f} | "
          f"复利(5%/笔):1000 → {eq:,.1f} ({(eq/1000-1)*100:+.1f}%)")
    return n, m, t, eq


summary(rows, "现状:全部 14 天往返")

new = [r for r in rows if is_maker_entry(r) and 15 <= hold(r) <= 50]
summary(new, "新设计:挂单+15~45s 持有")

new2 = [r for r in new if hour(r) >= 6]
summary(new2, "新设计+排除夜间")

print("\n== 经验桶 edge(前 7 天建桶 → 后 7 天验证)== ")
half = now - 7 * 86400
train = [r for r in new2 if float(r.get("ts") or 0) < half]
test = [r for r in new2 if float(r.get("ts") or 0) >= half]


def bucket_key(r):
    return (str(r.get("symbol") or "?").upper(),
            "夜" if hour(r) < 6 else "晨" if hour(r) < 9
            else "午" if hour(r) < 18 else "晚")


def bucket_edges(rs, min_n=10):
    g = defaultdict(list)
    for r in rs:
        g[bucket_key(r)].append(float(r["y_bp"]))
    return {k: (float(np.mean(v)), len(v)) for k, v in g.items()
            if len(v) >= min_n}


edges = bucket_edges(train)
print(f"建桶({len(train)} 条训练往返)⇒ 桶数 {len(edges)}")
test_kept = []
for r in test:
    k = bucket_key(r)
    e = edges.get(k)
    if e is None:
        continue
    if e[0] <= 0:
        continue
    test_kept.append((float(r["y_bp"]), min(0.05, max(0.01, e[0] / 100.0))))
if test_kept:
    eq = 1000.0
    for y, w in test_kept:
        eq *= (1.0 + w * y / 100.0)
    print(f"凯利定仓验证:后 7 天 {len(test_kept)} 笔 | "
          f"1000 → {eq:,.1f} ({(eq/1000-1)*100:+.1f}%)")
else:
    print("凯利定仓:验证样本不足")
