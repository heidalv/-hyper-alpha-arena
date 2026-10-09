# -*- coding: utf-8 -*-
"""[h902] 最深入调查:概率分析为什么错 + 高频到底怎么赚钱。

问题链:
  ① 概率分析为什么错 —— 校准实测:门模型的 mu 预测 vs 实际实现的 y;
     以及"预测方向"这条路本身的极限(markout ≈ 0)。
  ② 高频到底怎么赚钱 —— 把全部往返按可观测条件分桶,
     找出**哪些条件的平均 y 显著非零**(t 检验),哪些是零。
     结论直接给出"钱在哪、概率该测什么"。
"""
import io
import json
import math
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
print(f"往返总数: {len(rows)}")

now = time.time()
recent = [r for r in rows if float(r.get("ts") or 0) > now - 14 * 86400]
print(f"近 14 天: {len(recent)}")


def tstat(ys):
    ys = np.array(ys, dtype=float)
    if len(ys) < 2:
        return float(np.mean(ys)), 0.0, len(ys)
    m = float(np.mean(ys))
    se = float(ys.std(ddof=1) / np.sqrt(len(ys)))
    return m, (m / se if se > 0 else 0.0), len(ys)


def bucket(report, rows, keyfn, name, min_n=15):
    groups = defaultdict(list)
    for r in rows:
        groups[keyfn(r)].append(float(r["y_bp"]))
    print(f"\n== {name} ==")
    for k in sorted(groups, key=lambda k: -len(groups[k])):
        ys = groups[k]
        if len(ys) < min_n:
            continue
        m, t, n = tstat(ys)
        star = "***" if abs(t) >= 2.6 else "**" if abs(t) >= 1.96 else \
            "*" if abs(t) >= 1.28 else ""
        print(f"  {str(k):<28} n={n:>4} mean={m:+7.2f}bp t={t:+5.2f} {star}")


# ① 校准:门模型的 mu 预测 vs 实现(用近 14 天的真实往返)
print("\n" + "=" * 70)
print("① 概率分析为什么错 —— 校准实测")
print("=" * 70)
# 当前门模型对每个币给了 mu;取近 14 天每币的实际平均 y 对比
sym_mu, sym_y = {}, defaultdict(list)
try:
    gate = json.loads((ROOT / "data" / "flow_gate_last.json").read_text(
        encoding="utf-8"))
    for s, g in (gate.get("gates") or {}).items():
        if g.get("mu") is not None:
            sym_mu[str(s).upper()] = float(g["mu"])
except Exception:
    pass
for r in recent:
    sym_y[str(r.get("symbol") or "").upper()].append(float(r["y_bp"]))
print(f"币     模型mu   实际均值  n   方向对否")
agree, disagree, total = 0, 0, 0
for s in sorted(sym_mu, key=lambda s: -sym_mu[s]):
    ys = sym_y.get(s)
    if not ys or len(ys) < 10:
        continue
    mu, actual = sym_mu[s], float(np.mean(ys))
    ok = (mu > 0) == (actual > 0)
    total += 1
    agree += int(ok)
    disagree += int(not ok)
    print(f"  {s:<6} {mu:+8.2f} {actual:+9.2f} {len(ys):>4}  {'✓' if ok else '✗'}")
print(f"  方向对率: {agree}/{total} = {agree/max(1,total)*100:.0f}%")

# ② 高频到底怎么赚钱:全部条件分解
print("\n" + "=" * 70)
print("② 高频到底怎么赚钱 —— 可观测条件的 edge 分解(近 14 天)")
print("=" * 70)


def hour_bucket(r):
    h = time.localtime(float(r.get("ts") or 0)).tm_hour
    return ("夜间 0-5" if h < 6 else "清晨 6-8" if h < 9
            else "上午 9-13" if h < 14 else "午后 14-17" if h < 18
            else "晚间 18-23")


bucket(report=None, rows=recent, keyfn=hour_bucket, name="时段", min_n=20)
bucket(report=None, rows=recent,
       keyfn=lambda r: f"持有 {int(float(r.get('hold_sec') or 0)//15)*15}s",
       name="持有时间(15s 档)", min_n=20)
bucket(report=None, rows=recent,
       keyfn=lambda r: f"策略 {r.get('strategy') or '?'}", name="策略", min_n=15)
bucket(report=None, rows=recent,
       keyfn=lambda r: ("吃单" if float(r.get("fee_bp") or 0) > 0 else "挂单"),
       name="进场方式", min_n=20)
bucket(report=None, rows=recent,
       keyfn=lambda r: f"离场 {r.get('why') or '?'}", name="离场原因", min_n=15)
