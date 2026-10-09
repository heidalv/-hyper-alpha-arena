"""H73：H71 的 Ridge 到底学到了什么？—— 因子系数与单因子 IC

H71 的 Ridge 样本外 IC +0.0534、分档单调 ⇒ **信号是真的**，只是幅度小。
那么在投更多算力之前，必须先回答：**信号来自哪些因子？**

  · 若信号集中在 1~2 个因子（如 OFI）⇒ 加更多同源因子无用，ML 已到顶
  · 若信号均匀散布在十几个因子上 ⇒ 还有因子挖掘空间
  · 若系数符号与金融直觉相反 ⇒ 要查是发现了新东西还是过拟合

同时算**每个因子单独的样本外 IC**，作为"因子挖掘还有多少空间"的判据。

用法：
    .venv\\Scripts\\python.exe scripts\\h73_factor_attribution.py --hours 72
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")


def _spearman(x, y):
    import numpy as np
    x = np.asarray(x, float); y = np.asarray(y, float)
    m = np.isfinite(x) & np.isfinite(y)
    x, y = x[m], y[m]
    if len(x) < 60 or x.std() == 0 or y.std() == 0:
        return float("nan"), len(x)
    def _rank(a):
        o = a.argsort(); r = np.empty(len(a), float); r[o] = np.arange(len(a), dtype=float)
        _, inv, cnt = np.unique(a, return_inverse=True, return_counts=True)
        if (cnt > 1).any():
            s = np.zeros(len(cnt)); np.add.at(s, inv, r); r = (s / cnt)[inv]
        return r
    rx, ry = _rank(x), _rank(y)
    return float(np.corrcoef(rx, ry)[0, 1]), len(x)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=72.0)
    ap.add_argument("--symbols", default="BTC,ETH,SOL,XRP,DOGE")
    a = ap.parse_args()

    import numpy as np

    from h71_tail_ml import build

    syms = [s.strip().upper() for s in a.symbols.split(",") if s.strip()]
    Xs, ys, keys = [], [], None
    for s in syms:
        d = build(s, a.hours, 0.9, 0.05, 0.0, 30.0)
        if d and len(d["X"]) > 500:
            Xs.append(d["X"]); ys.append(d["y"]); keys = d["keys"]
    if not Xs:
        print("无数据")
        return 1
    X = np.vstack(Xs); y = np.concatenate(ys)
    m = np.isfinite(y) & np.isfinite(X).all(1)
    X, y = X[m], y[m]
    print("=" * 100)
    print("H73  因子归因：信号来自哪里？还有多少挖掘空间？")
    print("=" * 100)
    print(f"  样本 {len(y):,}  因子 {X.shape[1]}  基准均值 {y.mean():+.4f}bp")

    # ── 1) 单因子全样本 Spearman IC ──
    print(f"\n  {'因子':<20} {'Spearman IC':>13} {'|IC|':>8}")
    print("  " + "-" * 44)
    ics = {}
    for j, k in enumerate(keys):
        rho, n = _spearman(X[:, j], y)
        ics[k] = rho
        print(f"  {k:<20} {rho:>+13.4f} {abs(rho):>8.4f}")

    # ── 2) Ridge 系数（标准化后，全样本）──
    print("\n  ── Ridge 系数（标准化，全样本拟合；符号 = 方向）──")
    mu, sd = X.mean(0), X.std(0) + 1e-12
    Xs_ = (X - mu) / sd
    A = np.column_stack([np.ones(len(Xs_)), Xs_])
    w = np.linalg.solve(A.T @ A + 1.0 * np.eye(A.shape[1]), A.T @ y)
    print(f"\n  {'因子':<20} {'系数':>12} {'与IC同号?':>10}")
    print("  " + "-" * 46)
    agree = 0
    for j, k in enumerate(keys):
        same = "✓" if np.sign(w[j + 1]) == np.sign(ics[k]) else "✗"
        if same == "✓":
            agree += 1
        print(f"  {k:<20} {w[j+1]:>+12.5f} {same:>10}")
    print(f"\n  系数与 IC 同号的因子：{agree}/{len(keys)}"
          f"  （同号比例高 ⇒ 线性关系稳定，非线性空间小）")

    # ── 3) 信号集中度 ──
    a_ = np.abs(w[1:])
    share = np.sort(a_)[::-1] / a_.sum()
    print(f"\n  ── 信号集中度（按 |系数| 归一）──")
    print(f"    最大单因子占比 {share[0]*100:.1f}%   前 3 个合计 {share[:3].sum()*100:.1f}%")
    top = np.argsort(-a_)[:5]
    print(f"    权重最高的 5 个：{[(keys[i], round(float(w[i+1]),5)) for i in top]}")

    print("\n" + "=" * 100)
    print("判读")
    print("=" * 100)
    if share[0] > 0.50:
        print(f"  ⇒ 信号**高度集中**在单因子（{keys[int(top[0])]}，{share[0]*100:.0f}%）")
        print("     ⇒ 加更多同源因子收益有限；应换**信息源不同**的因子")
    elif share[:3].sum() > 0.75:
        print(f"  ⇒ 信号集中在 3 个因子内（{share[:3].sum()*100:.0f}%）")
    else:
        print("  ⇒ 信号**分散**在多个因子 ⇒ 因子挖掘仍有空间")
    if agree < len(keys) * 0.6:
        print("  ⇒ 系数与单因子 IC 符号大量不一致 ⇒ 存在共线性/过拟合风险，需查")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
