"""H95：把「出库是唯一障碍」变成可执行的**盈亏平衡门槛**。

# H94 的完整成本桥（今天，残差 0.0%）

```
① 入场腿 pnl    +0.8628 USD   **+0.102 bp/周期**   ← 入场侧为正
② 入场腿 fee     0.0000 USD     +0.000
③ 强平腿 pnl   −15.3744 USD     −1.825 bp/周期
④ 强平腿 fee    −8.5433 USD     −1.014 bp/周期
────────────────────────────────────────────
合计          −23.0549 USD     −2.737 bp/周期
```

今天 624 周期、145 个含强平（23.2%）。

# 这个脚本算什么（把结论变成决策依据）

**关键问题：光靠"降低强平率"够不够？**

  · 每周期强平成本 = 23.9176 / 624 = **0.03833 USD/周期 = 2.839 bp/周期**
  · 每次强平成本   = 23.9176 / 145 = **0.16495 USD/次  ≈ 12.22 bp/次**
  · 入场侧贡献     = +0.8628 / 624 = **+0.00138 USD/周期 = +0.102 bp/周期**

⇒ 要打平需要：`入场侧 + 入场侧×(未强平比例) ...` 严格写：

    每周期净额 = A×(1−p) + (A − C)×p
      A = 无强平周期的净额（入场贡献）
      C = 每次强平的额外成本
      p = 强平率

⇒ **打平条件：`A − C×p = 0`  ⇒  `p* = A / C`**

# 判据（事先定死）

  · 若 `p*` **远低于**当前 p（比如 <1/3）⇒ 单靠降强平率**不够**，
    必须同时降低单次强平成本 C
  · 若 `p*` 与当前 p 同量级 ⇒ 降强平率是可行路径
  · 明确给出"C 需要降到多少"的备选门槛

用法：
    .venv\\Scripts\\python.exe scripts\\h95_breakeven_threshold.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

NOTIONAL = 135.0
# H94 实测（今天 09-21）
FILL_PNL = +0.8628      # 入场腿 pnl
FILL_FEE = 0.0000
FLAT_PNL = -15.3744     # 强平腿 pnl
FLAT_FEE = -8.5433      # 强平腿 fee
N_EP = 624
N_FLAT = 145


def main():
    print("=" * 96)
    print("H95  盈亏平衡门槛：光降强平率够不够？")
    print("=" * 96)

    tot = FILL_PNL + FILL_FEE + FLAT_PNL + FLAT_FEE
    p = N_FLAT / N_EP
    print(f"\n  实测：{N_EP} 周期，{N_FLAT} 个含强平（**p = {p*100:.1f}%**）")
    print(f"  完整口径合计 {tot:+.4f} USD = {tot/N_EP:+.5f} USD/周期 "
          f"= {tot/N_EP/NOTIONAL*1e4:+.3f} bp/周期")

    # A：无强平周期的每周期净额（入场侧贡献，按全部周期摊）
    A = (FILL_PNL + FILL_FEE) / N_EP
    # C：每次强平的额外成本
    C = -(FLAT_PNL + FLAT_FEE) / N_FLAT
    print(f"\n  A（入场侧每周期贡献） = {A:+.5f} USD = {A/NOTIONAL*1e4:+.3f} bp")
    print(f"  C（每次强平成本）     = {C:+.5f} USD = {C/NOTIONAL*1e4:+.3f} bp")

    # 校验：A - C*p 应等于实测每周期净额
    chk = A - C * p
    print(f"\n  校验 A − C×p = {chk:+.5f} USD/周期 "
          f"（实测 {tot/N_EP:+.5f}）⇒ {'一致 ✓' if abs(chk-tot/N_EP)<1e-6 else '不一致 ✗'}")

    # 打平门槛
    p_star = A / C
    print("\n" + "=" * 96)
    print("门槛 1：只降强平率（C 不变）")
    print("=" * 96)
    print(f"\n  p* = A / C = {A:.5f} / {C:.5f} = **{p_star*100:.2f}%**")
    print(f"  当前 p = {p*100:.1f}%  ⇒ 需要降到 **{p_star*100:.2f}%**"
          f"（降幅 {(1-p_star/p)*100:.1f}%）")

    print("\n" + "=" * 96)
    print("门槛 2：只降单次强平成本 C（p 不变）")
    print("=" * 96)
    C_star = A / p
    print(f"\n  C* = A / p = {A:.5f} / {p:.4f} = **{C_star:.5f} USD**"
          f" = {C_star/NOTIONAL*1e4:.3f} bp")
    print(f"  当前 C = {C:.5f} USD = {C/NOTIONAL*1e4:.3f} bp"
          f"  ⇒ 需要降到 **{(1-C_star/C)*100:.1f}%**（即降 {C-C_star:.5f} USD）")
    print(f"\n  现实性检查：C 的构成 =")
    print(f"     · taker 费        = {abs(FLAT_FEE)/N_FLAT:.5f} USD"
          f" = {abs(FLAT_FEE)/N_FLAT/NOTIONAL*1e4:.2f} bp")
    print(f"     · 价差穿越 + 不利移动 = {abs(FLAT_PNL)/N_FLAT:.5f} USD"
          f" = {abs(FLAT_PNL)/N_FLAT/NOTIONAL*1e4:.2f} bp")
    print(f"     · 其中 taker 费**不可能降到 0 以下**（除非换场地/拿返佣）")
    print(f"     ⇒ C* = {C_star:.5f} USD 里，光 taker 就要 "
          f"{abs(FLAT_FEE)/N_FLAT:.5f} USD")
    if abs(FLAT_FEE) / N_FLAT >= C_star:
        print(f"     ⇒ **⚠️ C* < 单笔 taker 费 ⇒ 只靠降 C 也打不平**，必须两者同时做")

    print("\n" + "=" * 96)
    print("门槛 3：两者同时（给几个组合看可行性）")
    print("=" * 96)
    print(f"\n  {'目标 p':>8} {'所需 C USD':>12} {'所需 C bp':>10} "
          f"{'扣 taker 后余额 bp':>18} {'可行?':>8}")
    print("  " + "-" * 62)
    taker_bp = abs(FLAT_FEE) / N_FLAT / NOTIONAL * 1e4
    for p_t in (0.20, 0.15, 0.10, 0.05, 0.02, 0.01):
        C_need = A / p_t
        rest = C_need / NOTIONAL * 1e4 - taker_bp
        ok = "是" if rest > 0 else "**否**"
        print(f"  {p_t*100:>7.0f}% {C_need:>12.5f} {C_need/NOTIONAL*1e4:>10.3f} "
              f"{rest:>18.3f} {ok:>8}")
    print(f"\n  （'扣 taker 后余额' = 允许留给'价差穿越+不利移动'的额度；")
    print(f"    ≤0 意味着**连 taker 费都不够覆盖** ⇒ 那条路不可行）")

    print("\n" + "=" * 96)
    print("结论")
    print("=" * 96)
    print(f"\n  · 入场侧是**正的**（{A/NOTIONAL*1e4:+.3f} bp/周期）⇒ 不要动它")
    print(f"  · 打平需 p ≤ **{p_star*100:.2f}%**（当前 {p*100:.1f}%）")
    print(f"  · 或 C ≤ **{C_star/NOTIONAL*1e4:.3f} bp**（当前 {C/NOTIONAL*1e4:.3f} bp）")
    print(f"  · **单次强平里有 {taker_bp:.2f} bp 是 taker 费，谁都消不掉**")
    print(f"    ⇒ 这就是为什么「换场地拿 0 taker」（Lighter 0/0）在测算上值那么多")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
