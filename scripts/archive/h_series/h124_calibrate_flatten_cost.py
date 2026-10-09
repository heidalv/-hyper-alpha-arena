# -*- coding: utf-8 -*-
"""[H124 2026-09-21] 校准「一次强平值多少 bp」—— 让选币模型的分母站得住。

# 为什么必须先校准

H123 的候选模型用 `capture_bp − flatten_rate × 每周期强平成本` 排序。若那个
"每周期强平成本"量错，整个排序就是错的。实测显示可疑：

    ASTER 捕获 +0.600bp、强平率 6.4%  ⇒ 若成本 10.66bp，E = +0.600 − 0.68 = **−0.08bp**
    但 ASTER 的实际每周期净额是**正的**（净 +4.79 USD / 594 周期）

⇒ 要么"每周期强平成本"远小于 10.66bp，要么"捕获"的口径不是每周期。
本脚本用**真实的每周期金额**反推这两个量，不再靠估。

# 口径

  · 每周期净额（USD） = 该周期内所有成交的 (进场价差 − 强平成本 − 手续费)
    —— 直接用 H122 的分币净额 ÷ 周期数，**这是最硬的口径**
  · 每周期名义（USD） = 周期峰值名义的中位
  · ⇒ 每周期净额(bp) = 净额(USD) / 名义(USD) × 1e4
  · 每周期强平成本(bp) = 强平腿净额 / 名义 × 1e4 ÷ 强平率   ← 反推

用法：
    .venv\\Scripts\\python.exe scripts\\h124_calibrate_flatten_cost.py
"""
from __future__ import annotations

import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from h122_symbol_scorecard import compute  # noqa: E402

SINCE = "2026-09-21T00:00:00"


def main() -> int:
    m = compute(SINCE)
    print("=" * 104)
    print("H124  校准：每周期净额(bp) 与 每周期强平成本(bp)")
    print("=" * 104)
    print(f"  窗口 {SINCE} 起\n")

    print(f"  {'币':<10} {'周期':>5} {'强平率':>7} {'净额$':>9} {'峰值名义$':>11} "
          f"{'每周期净bp':>11} {'强平率×成本=净':>15}")
    print("  " + "-" * 84)
    rows = []
    for s, v in sorted(m.items(), key=lambda kv: kv[1]["net_usd"]):
        cyc = v.get("cycles") or 0
        if cyc < 20:
            continue
        notl = v.get("peak_notional_med") or 0.0
        if notl <= 0:
            continue
        net_bp = v["net_usd"] / notl * 1e4 / cyc
        fr = v.get("flatten_rate") or 0.0
        rows.append({"s": s, "cyc": cyc, "fr": fr, "net_usd": v["net_usd"],
                     "notl": notl, "net_bp": net_bp})
        print(f"  {s:<10} {cyc:>5} {fr*100:>6.1f}% {v['net_usd']:>9.3f} {notl:>11.2f} "
              f"{net_bp:>11.4f}")

    if not rows:
        print("  样本不足")
        return 1

    # 每周期强平成本反推：把 net_bp 对 flatten_rate 做一元回归（过原点截距另算）
    # net_bp = capture_bp_cyc − fr × cost_per
    # ⇒ 用两个点解：取 fr 最小与最大的两个币
    rows.sort(key=lambda r: r["fr"])
    lo, hi = rows[0], rows[-1]
    print("\n" + "-" * 84)
    print("  用线性关系反推（net_bp = capture_cyc − fr × cost_per）：")
    print(f"    最低强平率 {lo['s']:<8} fr={lo['fr']*100:5.1f}%  net={lo['net_bp']:+.4f}bp")
    print(f"    最高强平率 {hi['s']:<8} fr={hi['fr']*100:5.1f}%  net={hi['net_bp']:+.4f}bp")
    dfr = hi["fr"] - lo["fr"]
    if abs(dfr) > 1e-6:
        # 两点法：假设两币 capture_cyc 相同（弱假设，仅用于量级）
        cost = -(hi["net_bp"] - lo["net_bp"]) / dfr
        cap = lo["net_bp"] + lo["fr"] * cost
        print(f"\n    => 解出：每周期捕获 ≈ **{cap:+.4f} bp**   每周期强平成本 ≈ **{cost:.4f} bp**")
        print("      （前述模型用的 10.66bp 是【单次强平的美元成本折算】，")
        print("        不是【每周期】口径 —— 两者差一个 (强平腿数/周期) 的因子）")

    # 更稳的做法：只用「已实现每周期净额」排序，不拆解
    print("\n" + "-" * 84)
    print("  ⇒ 结论：**不要拆解**。直接用实测「每周期净额 bp」排序，")
    print("     它的口径最硬（分子分母都来自真实成交），不必假设 capture/cost 分解。")
    print(f"\n  {'币':<10} {'每周期净bp':>11}  排名")
    print("  " + "-" * 34)
    for i, r in enumerate(sorted(rows, key=lambda r: -r["net_bp"]), 1):
        print(f"  {r['s']:<10} {r['net_bp']:>11.4f}  {i}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
