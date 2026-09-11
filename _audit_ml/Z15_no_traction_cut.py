# -*- coding: utf-8 -*-
"""Z15：「无动能认错」（fast_cut 同族）在 mid/long 车道是否成立？

机制来源：scalp 车道已有成熟机制 `SCALP_EXIT_FAST_CUT_*`
（持仓 ≥30min 且峰值 <0.3% 且浮亏 ≤-0.2% → 认错全平）。
Z10 网格中 mid 层最优恰为同族：**峰值 <0.3% 时把止损收到 2%**（+$5.91 vs 实际 -$48.12）。

本轮把它做严谨：
- 变体 A（止损式）：峰值 < P → 硬止损 -X%（否则不追加止损，保留真实出场）；
- 变体 B（认错式）：持仓 ≥ T 小时 且 峰值 < P 且 当前 ≤ -L% → 当根 1h 收盘平仓；
- 指标：总 USD / 均值% / 胜率 / **模式率** / ≤-2% / ≤-3% 笔数 / 逐月；
- 稳健性：**3 折 walk-forward**（前 2/3 选参 → 后 1/3 验证）+ 全样本 bootstrap 95% CI。
"""
from __future__ import annotations

import os
import random
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend" / "scripts"))
sys.path.insert(0, str(ROOT / "_audit_ml"))

from Z10_sl_overlay import FEE_SIDE, SLIP, build  # noqa: E402

SEED = 20260910


def overlay(r, P, X, T=0.0, mode="stop"):
    """返回 (usd, triggered)。

    mode="stop"：峰值 < P 且持仓 ≥ T 小时 → 启用 -X% 硬止损；
    mode="review"：持仓 ≥ T 小时 且 峰值 < P 且 当前 ≤ -X% → 收盘平仓。
    """
    sign = 1.0 if r["side"] == "long" else -1.0
    s, i0 = r["s"], r["i"]
    parts, pi = r["parts"], 0
    realized, fees, qty = 0.0, 0.0, r["sz0"]
    peak = 0.0
    for k in range(i0, len(s)):
        ts, _o, h, l, c = s[k]
        while pi < len(parts) and parts[pi][0] <= ts:
            _t, q, px = parts[pi]
            q = min(q, qty)
            realized += q * sign * (px - r["entry"])
            fees += q * px * (FEE_SIDE + SLIP) * 2
            qty -= q
            pi += 1
        if qty <= 1e-12:
            break
        hold_h = (ts - s[i0][0]) / 3600.0
        armed = peak < P and hold_h >= T
        if armed:
            if mode == "stop":
                sl_price = r["entry"] * (1 - sign * X / 100.0)
                hit = (l <= sl_price) if sign > 0 else (h >= sl_price)
                if hit:
                    fill = sl_price * (1 - sign * SLIP)
                    realized += qty * sign * (fill - r["entry"])
                    fees += qty * fill * (FEE_SIDE + SLIP) * 2
                    return realized - fees, True
            else:
                cur_pct = sign * (c - r["entry"]) / r["entry"] * 100
                if cur_pct <= -X:
                    realized += qty * sign * (c - r["entry"])
                    fees += qty * c * (FEE_SIDE + SLIP) * 2
                    return realized - fees, True
        hi = sign * (h - r["entry"]) / r["entry"] * 100
        peak = max(peak, hi)
        if ts >= r["close_ts"]:
            break
    return r["base_usd"], False


def metrics(sub, fn):
    usds, trig = [], 0
    for r in sub:
        u, t = fn(r)
        usds.append(u)
        trig += int(t)
    pcts = [u / r["notional0"] * 100 for u, r in zip(usds, sub)]
    pat = sum(1 for u, r in zip(usds, sub)
              if r["db_peak"] >= 0.5 and u < 0)
    bym = defaultdict(float)
    for u, r in zip(usds, sub):
        bym[r["mon"]] += u
    return {
        "usd": sum(usds), "mean": sum(pcts) / len(pcts),
        "win": sum(1 for x in pcts if x > 0) / len(pcts),
        "pat": pat / len(sub), "le2": sum(1 for x in pcts if x <= -2),
        "le3": sum(1 for x in pcts if x <= -3), "trig": trig,
        "bym": dict(bym),
    }


def row(label, m):
    bym_s = " ".join(f"{k[5:]}:{v:+.0f}" for k, v in sorted(m["bym"].items()))
    print(f"{label:<26}{m['usd']:>+10.2f}{m['mean']:>+9.3f}{m['win']:>7.3f}"
          f"{m['pat']:>8.3f}{m['le2']:>6}{m['le3']:>6}{m['trig']:>6}  {bym_s}")


def main() -> int:
    recs = build(75)
    variants = [("实际（无附加规则）", None)]
    for P in (0.3, 0.5, 1.0):
        for X in (1.5, 2.0, 2.5, 3.0):
            variants.append((f"A 峰<{P}% → SL{X}%", ("stop", P, X, 0.0)))
    for P in (0.3, 0.5):
        for X in (1.0, 1.5, 2.0):
            for T in (2.0, 4.0):
                variants.append((f"B 峰<{P}%·{T:g}h·-{X}%", ("review", P, X, T)))

    subsets = [("全量", recs), ("mid", [r for r in recs if r["tier"] == "mid"]),
               ("mid·门放行", [r for r in recs if r["tier"] == "mid" and r.get("allow")]),
               ("mid·门拦截", [r for r in recs if r["tier"] == "mid" and not r.get("allow")]),
               ("long", [r for r in recs if r["tier"] == "long"])]
    print(f"样本 n={len(recs)}（近 75 天）")
    for label, sub in subsets:
        if not sub:
            continue
        print(f"\n=== {label} n={len(sub)} ===")
        print(f"{'方案':<26}{'总USD':>10}{'均值%':>9}{'胜率':>7}{'模式率':>8}"
              f"{'≤-2%':>6}{'≤-3%':>6}{'触发':>6}  逐月USD")
        for name, args in variants:
            if args is None:
                row(name, metrics(sub, lambda r: (r["base_usd"], False)))
            else:
                mode, P, X, T = args
                row(name, metrics(sub, lambda r, m=mode, p=P, x=X, t=T: overlay(r, p, x, t, m)))

    # ---- 3 折 walk-forward + bootstrap ----
    print("\n===== 3 折 walk-forward（前 2/3 选参 → 后 1/3 验证）=====")
    cands = [v for v in variants if v[1] is not None]
    for label, sub in subsets:
        if len(sub) < 45:
            print(f"\n--- {label} n={len(sub)} 样本不足 ---")
            continue
        cut = int(len(sub) * 2 / 3)
        tr, te = sub[:cut], sub[cut:]
        best, best_usd = None, None
        for name, (mode, P, X, T) in cands:
            u = sum(overlay(r, P, X, T, mode)[0] for r in tr)
            if best_usd is None or u > best_usd:
                best, best_usd = (name, mode, P, X, T), u
        name, mode, P, X, T = best
        mb = metrics(te, lambda r: (r["base_usd"], False))
        mv = metrics(te, lambda r: overlay(r, P, X, T, mode))
        print(f"\n--- {label} 训练n={len(tr)} 验证n={len(te)} 训练最优={name} ---")
        print(f"  验证：实际 ${mb['usd']:+.2f} → ${mv['usd']:+.2f}（差 ${mv['usd']-mb['usd']:+.2f}）"
              f" 模式率 {mb['pat']:.3f}→{mv['pat']:.3f} ≤-2% {mb['le2']}→{mv['le2']} "
              f"触发{mv['trig']}")
        diffs = [overlay(r, P, X, T, mode)[0] / r["notional0"] * 100
                 - r["base_usd"] / r["notional0"] * 100 for r in te]
        rnd = random.Random(SEED)
        n = len(diffs)
        boots = sorted(sum(diffs[rnd.randrange(n)] for _ in range(n)) / n for _ in range(4000))
        obs = sum(diffs) / n
        lo, hi = boots[int(0.025 * 4000)], boots[int(0.975 * 4000)]
        print(f"  bootstrap pct 差={obs:+.4f}% CI[{lo:+.4f},{hi:+.4f}] "
              f"{'显著' if lo > 0 or hi < 0 else '不显著(跨0)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
