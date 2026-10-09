"""h483：`ofi_confirm_threshold` 的**样本外**阈值选择（③ 的最终决策依据）。

为什么必须做（否则就是数据窥探）：
  h482 在 4 个候选阈值（0.6/0.7/0.8/0.9）里挑了最好的一个 ⇒ 多重比较。
  0.9 的 p<0.001 看着强，但"挑出来的最好"天然偏高 ⇒ 必须**在独立样本上复现**。

方法：把 168h 采样按时间**前后各半**（A 半 = 较早，B 半 = 较晚），
对每个候选阈值 θ 在**两半上分别**计算：
  · 保留集 {fo≥θ} 与放弃集 {0.5≤fo<θ} 的互斥分带 Welch Δ / t；
  · 换算腿速（以该半的 θ=0.5 通过率为基准，按通过率比值对实测腿速缩放）。
判定：**两半都显著为正、且两半腿速都 ≥60/h** 的阈值才算通过；
     只在 A 半显著 = 已失效；只在 B 半显著 = 未经检验。

顺带打印 `fo = OFI×d` 的分布（判断该特征是否近乎二值化——
若是，阈值在高区的细分大多是噪声）。

用法：python scripts/h483_confirm_oos.py [--live-legs 78]
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
OUT = ROOT / "research_l1" / "out" / "h483_confirm_oos.json"
CANDS = (0.6, 0.7, 0.8, 0.9, 0.95)


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
    return {"mean_keep": round(m1, 3), "mean_drop": round(m2, 3),
            "n_keep": n1, "n_drop": n2, "delta": round(m1 - m2, 3),
            "t": round(t, 2), "p": round(min(1.0, 2 * s), 4)}


def analyse(s, live_legs, label):
    base_p = sum(1 for fo, _ in s if fo >= 0.5) / len(s)
    out = {}
    print(f"\n── {label}（n={len(s)}，θ=0.5 通过率 {base_p:.4f}）──")
    print(f"{'θ':>6s} {'Δ(保留−放弃)':>13s} {'t':>7s} {'p':>8s} "
          f"{'腿速/h':>8s} {'保留n':>7s} {'放弃n':>7s}")
    for th in CANDS:
        keep = [y for fo, y in s if fo >= th]
        drop = [y for fo, y in s if 0.5 <= fo < th]
        w = welch(keep, drop)
        if not w:
            continue
        p_th = sum(1 for fo, _ in s if fo >= th) / len(s)
        lh = live_legs * p_th / base_p if base_p else 0.0
        w["legs_h"] = round(lh, 1)
        out[str(th)] = w
        print(f"{th:>6.2f} {w['delta']:>+13.2f} {w['t']:>+7.2f} {w['p']:>8.3f} "
              f"{lh:>8.1f} {w['n_keep']:>7d} {w['n_drop']:>7d}")
    return out, base_p


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live-legs", type=float, default=78.0)
    a = ap.parse_args()
    j = json.loads(CACHE.read_text(encoding="utf-8"))
    s = [(float(x), float(y)) for x, y in j["s"]]
    print(f"样本 {len(s)}（窗口 {j.get('hours')}h）")

    # 分布（判断是否近乎二值化）
    print("\nfo = OFI×d 分布：")
    for lo, hi in ((0.4, 0.5), (0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 0.9),
                   (0.9, 0.95), (0.95, 0.99), (0.99, 1.01)):
        n = sum(1 for fo, _ in s if lo <= fo < hi)
        ys = [y for fo, y in s if lo <= fo < hi]
        mu = sum(ys) / len(ys) if ys else float("nan")
        print(f"  [{lo:.2f},{hi:.2f}) n={n:6d} ({100*n/len(s):5.1f}%) "
              f"边际={mu:+6.2f}bp")

    half = len(s) // 2
    outA, bpA = analyse(s[:half], a.live_legs, "A 半（较早）")
    outB, bpB = analyse(s[half:], a.live_legs, "B 半（较晚）")
    outAll, _ = analyse(s, a.live_legs, "全样本")

    print("\n" + "=" * 92)
    print("样本外判定（两半都显著为正 且 两半腿速都 ≥60/h 才算通过）：")
    passed = []
    for th in CANDS:
        k = str(th)
        wa, wb = outA.get(k), outB.get(k)
        if not wa or not wb:
            continue
        ok = (wa["delta"] > 0 and wa["p"] <= 0.10 and wb["delta"] > 0 and wb["p"] <= 0.10
              and wa["legs_h"] >= 60 and wb["legs_h"] >= 60)
        print(f"  θ={th:.2f}: A Δ={wa['delta']:+.2f}(p={wa['p']:.3f},{wa['legs_h']:.0f}/h) "
              f"| B Δ={wb['delta']:+.2f}(p={wb['p']:.3f},{wb['legs_h']:.0f}/h) "
              f"⇒ {'**通过**' if ok else '不过'}")
        if ok:
            passed.append((th, wa, wb))
    if passed:
        # 通过者里取"全样本 t 最大"的那个（兼顾效应量与稳定性）
        th, wa, wb = max(passed, key=lambda x: outAll[str(x[0])]["t"])
        verdict = (f"**部署 θ={th}**：两半独立复现（A Δ={wa['delta']:+.2f} p={wa['p']:.3f}"
                   f"；B Δ={wb['delta']:+.2f} p={wb['p']:.3f}），腿速 "
                   f"{outAll[str(th)]['legs_h']:.0f}/h（≥60 硬约束）")
    else:
        verdict = ("**没有阈值通过样本外验证** ⇒ 不做这次阈值变更；"
                   "任务书里的 0.5→0.7 尤其站不住（全样本 p=0.77）")
    print("\n⇒ 裁决:", verdict)
    OUT.write_text(json.dumps(
        {"n": len(s), "live_legs": a.live_legs, "half_a": outA, "half_b": outB,
         "all": outAll, "verdict": verdict}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    print("已写:", OUT.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
