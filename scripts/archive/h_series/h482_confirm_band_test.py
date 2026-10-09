"""h482：`ofi_confirm_threshold` 0.5→? 的**互斥分带**显著性检验（③ 的决策量）。

为什么要单独做（h447 的曲线不能直接用来做这个决策）：
  · h447 打印的是**嵌套集合**的均值：`{fo ≥ θ}` 随 θ 变小时包含更多样本，
    两个阈值的均值差**不是**"多出来的那批样本"的效应；
  · 且 h447 的网格里**没有 0.70**（GRID=0/0.1/0.15/0.2/0.25/0.3/0.4/0.5/0.6/0.8）
    ⇒ 任务书里的"0.5→0.7（净/腿 +0.03→+0.14bp）"其实是 **0.5→0.8** 那一行
    （0.6 那点反而略差），标成 0.7 属于**取点错误**。

本脚本做正确的事：把样本切成**互斥带**，对"要不要把阈值推到 0.8"给出
Welch 检验 + 频率约束换算：

  · 带 A = {0.5 ≤ fo < 0.8}（现状会做、提到 0.8 后**被放弃**的那批）
  · 带 B = {fo ≥ 0.8}（提到 0.8 后**保留**的那批）
  · 若 mean(B) 显著 > mean(A) ⇒ 提高阈值是**筛选**（丢掉更差的样本）⇒ 部署 0.8；
  · 若不显著 ⇒ 提高阈值只是**减少腿量**（相同质量）⇒ 应保持 0.5。

腿速换算：以线上实测（θ=0.5 ⇒ 78 腿/h）为单位，按通过率比值缩放。

用法：python scripts/h482_confirm_band_test.py [--live-legs 78] [--cache-hours 168]
"""
from __future__ import annotations

import argparse
import json
import math
import pathlib
import sys

sys.stdout.reconfigure(encoding="utf-8")

ROOT = pathlib.Path(__file__).resolve().parents[1]
CACHE = ROOT / "research_l1" / "out" / "h447_samples_cache.json"
OUT = ROOT / "research_l1" / "out" / "h482_confirm_band.json"


def welch(a, b):
    n1, n2 = len(a), len(b)
    if n1 < 5 or n2 < 5:
        return None
    m1, m2 = sum(a) / n1, sum(b) / n2
    v1 = sum((x - m1) ** 2 for x in a) / (n1 - 1)
    v2 = sum((x - m2) ** 2 for x in b) / (n2 - 1)
    se = math.sqrt(v1 / n1 + v2 / n2)
    if se <= 0:
        return None
    t = (m1 - m2) / se
    df = (v1 / n1 + v2 / n2) ** 2 / ((v1 / n1) ** 2 / (n1 - 1) + (v2 / n2) ** 2 / (n2 - 1))

    def _pdf(x):
        return math.exp(math.lgamma((df + 1) / 2) - math.lgamma(df / 2)) / \
            math.sqrt(math.pi * df) * (1 + x * x / df) ** (-(df + 1) / 2)

    step, s, x = 0.05, 0.0, abs(t)
    while x < abs(t) + 30.0:
        s += step * (_pdf(x) + _pdf(x + step)) / 2
        x += step
    return {"mean_a": round(m1, 3), "mean_b": round(m2, 3), "n_a": n1, "n_b": n2,
            "delta": round(m1 - m2, 3), "t": round(t, 2),
            "p": round(min(1.0, 2 * s), 4)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live-legs", type=float, default=78.0,
                    help="θ=0.5 时的实测腿速（默认取现场 78/h）")
    ap.add_argument("--cache-hours", type=float, default=168.0)
    a = ap.parse_args()
    j = json.loads(CACHE.read_text(encoding="utf-8"))
    s = [(float(x), float(y)) for x, y in j["s"]]
    print(f"样本 {len(s)}（缓存 {CACHE.name}，窗口 {j.get('hours')}h）")
    base_p = sum(1 for fo, _ in s if fo >= 0.5) / len(s)
    print(f"基准 θ=0.5 通过率 {base_p:.4f} ⇔ 实测 {a.live_legs:.0f} 腿/h")

    def band(lo, hi):
        return [y for fo, y in s if lo <= fo < hi]

    def legs(th):
        p = sum(1 for fo, _ in s if fo >= th) / len(s)
        return a.live_legs * p / base_p if base_p else 0.0

    def mean_t(xs):
        if len(xs) < 5:
            return None
        m = sum(xs) / len(xs)
        v = sum((x - m) ** 2 for x in xs) / (len(xs) - 1)
        return m, (m / math.sqrt(v / len(xs)) if v > 0 else 0.0), len(xs)

    print("=" * 92)
    print(f"{'互斥带':>16s} {'n':>7s} {'边际@60s':>10s} {'t':>7s} "
          f"{'占总样本':>9s} {'换算腿/h':>10s}")
    bands = [(0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 1.01), (0.5, 1.01)]
    info = {}
    for lo, hi in bands:
        xs = band(lo, hi)
        r = mean_t(xs)
        if not r:
            print(f"{f'{lo}-{hi}':>16s} {len(xs):7d}  （样本不足）")
            continue
        m, t, n = r
        share = len(xs) / len(s)
        print(f"{f'{lo}-{hi}':>16s} {n:7d} {m:10.2f} {t:+7.1f} "
              f"{100*share:8.1f}% {a.live_legs*share/base_p:10.1f}")
        info[f"{lo}-{hi}"] = {"n": n, "mean": round(m, 3), "t": round(t, 2),
                              "share": round(share, 4)}
    print("=" * 92)
    # 逐个候选阈值：与"现状保留集"的互斥分带检验
    res = {}
    for th in (0.6, 0.7, 0.8, 0.9):
        kept = band(th, 1.01)          # 提到 th 后保留的
        drop = band(0.5, th)           # 提到 th 后放弃的
        w = welch(kept, drop)
        lh = legs(th)
        print(f"\n── 阈值 0.5 → {th} ──")
        if not w:
            print("   样本不足，无法检验")
            continue
        print(f"   保留集 {w['n_a']} 样本 边际 {w['mean_a']:+.2f}bp | "
              f"放弃集 {w['n_b']} 样本 边际 {w['mean_b']:+.2f}bp")
        print(f"   Δ(保留−放弃) = {w['delta']:+.2f}bp  t={w['t']:+.2f}  p={w['p']:.3f}")
        print(f"   换算腿速 {lh:.1f}/h（{'满足' if lh >= 60 else '**破 60/h 硬约束**'}）")
        gain = w["delta"] * w["n_a"] if w["delta"] > 0 else 0.0
        res[str(th)] = {**w, "legs_h": round(lh, 1), "gain_bp_legs": round(gain, 1)}
    print("=" * 92)
    sig = {k: v for k, v in res.items() if v.get("p", 1) <= 0.10 and v.get("delta", 0) > 0}
    best = None
    if sig:
        cand = [(k, v) for k, v in sig.items() if v["legs_h"] >= 60.0]
        if cand:
            best = max(cand, key=lambda kv: kv[1]["gain_bp_legs"])
    if best:
        verdict = (f"部署 θ={best[0]}：保留集的 60s 边际显著高于放弃集 "
                   f"（Δ={best[1]['delta']:+.2f}bp，t={best[1]['t']:+.2f}，"
                   f"p={best[1]['p']:.3f}），腿速 {best[1]['legs_h']:.1f}/h 仍达硬约束 ⇒ "
                   f"提高阈值是**筛选**，不是单纯减腿量")
    elif sig:
        verdict = ("有显著筛选效应，但候选阈值的腿速都 <60/h ⇒ **不可部署**（破用户硬约束）")
    else:
        verdict = ("**不部署**：0.5→0.6/0.7/0.8 的保留集与放弃集边际差不显著 ⇒ "
                   "提高阈值只会减少腿量、不改善质量")
    print("⇒ 裁决:", verdict)
    OUT.write_text(json.dumps(
        {"n_samples": len(s), "base_theta": 0.5, "live_legs": a.live_legs,
         "bands": info, "tests": res, "verdict": verdict},
        ensure_ascii=False, indent=2), encoding="utf-8")
    print("已写:", OUT.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
