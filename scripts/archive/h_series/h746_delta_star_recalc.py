# -*- coding: utf-8 -*-
"""[h746 2026-10-03] 恢复交易前的 δ* 重算(用 05:33 新捕获档 + 逐币 k)。

背景:曲面 05:33 刷新后捕获档结构变了(c 档 +5.51→+1.75、d 档 +1.23→**+3.24**),
原 h726 的 EV 最优(δ*=2.5 落 c 档)需要重算。
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

WIDTHS = [0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0]


def main() -> int:
    surf = json.loads((ROOT / "data" / "surface_fit_last.json").read_text(encoding="utf-8"))
    nb = surf.get("net_band") or {}
    print("新捕获档净:", json.dumps(nb, ensure_ascii=False))

    def band(cap: float) -> float:
        if cap < 0.5:
            return float(nb.get("a", 0.0))
        if cap < 1.0:
            return float(nb.get("b", 0.0))
        if cap < 2.0:
            return float(nb.get("c", 0.0))
        return float(nb.get("d", 0.0))

    print(f"\n  {'币':<10}{'k':>7}{'当前2.5期望':>12}{'最优δ*':>9}{'最优期望':>11}{'提升':>9}")
    rows = []
    for s in surf.get("per_symbol") or []:
        probs = s.get("probs")
        sym = s.get("symbol")
        if not probs or len(probs) != len(WIDTHS):
            continue
        lam = [-np.log(1 - max(0.01, min(0.99, p))) / 60.0 for p in probs]
        x, y = np.array(WIDTHS), np.log(np.array(lam))
        m = np.isfinite(y)
        A = np.vstack([np.ones(m.sum()), x[m]]).T
        coef, *_ = np.linalg.lstsq(A, y[m], rcond=None)
        Lam, k = float(np.exp(coef[0])), float(-coef[1])
        ev = lambda d: Lam * np.exp(-k * d) * 3600 * band(0.42 * d)   # noqa: E731
        cur = ev(2.5)
        best, bestv = 2.5, cur
        for d in np.linspace(0.5, 8.0, 76):
            v = ev(d)
            if v > bestv:
                best, bestv = d, v
        gain = (bestv / cur - 1) * 100 if cur > 0 else float("nan")
        rows.append((sym, k, cur, best, bestv, gain))
        print(f"  {sym:<10}{k:>7.3f}{cur:>12.0f}{best:>9.1f}{bestv:>11.0f}{gain:>+8.0f}%")
    if rows:
        opt = [r[3] for r in rows]
        print(f"\n  最优 δ* 中位数 {np.median(opt):.1f}bp | 范围 {min(opt):.1f}~{max(opt):.1f}bp"
              f" | 现行 ev_width_cap_bp=2.5")
        print("  结论:宽度敏感币(k 大)仍在 2.5;宽度不敏感币(k 小)应放宽到 4.5~6bp")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                      errors="replace", line_buffering=True)
    raise SystemExit(main())
