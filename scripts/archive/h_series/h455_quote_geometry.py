# -*- coding: utf-8 -*-
"""H455 报价几何的最优解：EV(δ, state) —— 每个状态该挂多远。

数学：对逆势方向 d（= 逆 r60），把挂单放在距中价 δ bp 处：
  · 成交条件：60s 内不利方向触及 δ，即 y60 ≤ −δ（y60 = d×Δmid(60s)，负=不利）
  · 成交后的捕获 = δ（买在 mid−δ / 卖在 mid+δ），随后承受的漂移 ≈ E[y60 | y60 ≤ −δ]
  ⇒ EV(δ, s) ≈ P(y60 ≤ −δ | s) × [ δ + E(y60 | y60 ≤ −δ, s) ]   （bp/次挂单）
对 δ ∈ 1..12bp 与各状态桶求 EV，取每状态最优 δ* —— 这就是"状态下该挂多近"的答案。
数据：h449 特征缓存（43,328 样本，168h）。
"""
import json
import math
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

CACHE = ROOT / "research_l1" / "out" / "h449_features_cache.json"
OUT = ROOT / "research_l1" / "out" / "h455_quote_geometry.json"
s = json.loads(CACHE.read_text(encoding="utf-8"))["s"]
print(f"样本 {len(s)}（列：fo, sigma, |r300|, y60）")

# 状态桶：确认强度 × 趋势强度
q35 = sorted(r[2] for r in s)[int(len(s) * 0.35)]
print(f"|r300| 的 35 分位 = {q35:.2f}bp")


def bucket(r):
    fo, sg, ar, y = r
    conf = fo >= 0.5
    strong = ar >= q35
    if conf and strong:
        return "confirm+强趋势"
    if conf:
        return "confirm+弱趋势"
    if fo >= 0.0:
        return "弱确认"
    return "逆流"


groups = {}
for r in s:
    groups.setdefault(bucket(r), []).append(r[3])
for k in groups:
    print(f"  {k:<14} n={len(groups[k]):>6}  平均 y60={sum(groups[k])/len(groups[k]):+.2f}bp")

rows = []
print(f"\n== EV(δ, state)（bp/次挂单）==")
print(f"{'状态':<14}" + "".join(f"δ={d:<6}" for d in (1, 2, 3, 5, 8, 12)))
best = {}
for k, ys in sorted(groups.items()):
    n = len(ys)
    line = []
    evs = {}
    for d in (1, 2, 3, 5, 8, 12):
        touched = [y for y in ys if y <= -d]
        if not touched:
            evs[d] = None
            line.append("   —    ")
            continue
        p = len(touched) / n
        cond = sum(touched) / len(touched)
        ev = p * (d + cond)
        evs[d] = round(ev, 4)
        line.append(f"{ev:>+7.3f} ")
    star = max((d for d in evs if evs[d] is not None), key=lambda d: evs[d], default=None)
    best[k] = {"n": n, "ev": evs, "delta_star": star, "ev_star": evs.get(star)}
    print(f"{k:<14}" + "".join(line) + f"  ⇒ δ*={star}bp (EV={evs.get(star)})")

print("\n== 判读 ==")
for k, v in best.items():
    print(f"  {k:<14} 最优挂距 {v['delta_star']}bp，期望 {v['ev_star']}bp/次")
print("\n（若各状态 δ* 明显不同 ⇒ 应按状态调整挂单距离 = '状态配额分配'的落地形式）")

OUT.write_text(json.dumps({"q35": q35, "buckets": best}, ensure_ascii=False, indent=2),
               encoding="utf-8")
print(f"已存 {OUT}")
