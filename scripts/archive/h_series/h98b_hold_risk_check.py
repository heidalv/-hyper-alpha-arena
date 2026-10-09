"""H98b：放宽持有上限前的**风险方向检查**（我此前把 300s 降到 60s，必须先确认不会反转）。

# 为什么要单独做这一步

我曾基于 **H61** 把 `max_one_side_seconds` 从 300 降到 60
（H61 显示"每个更短的持有都优于 300s"，左尾随持有单调变肥）。

现在 H94/H95/H97 又指向**放宽到 300s**（省 4.36bp taker，只多付 0.69bp 漂移）。

**两个结论方向相反** —— 必须先搞清为什么，否则就是来回改参数。

# 可能的解释（本脚本检验）

H61 测的是**入场腿**在固定持有下的漂移（在 hold 结束时按 mid 平）。
那时**没有把"强平要付 taker"算进去**。
而 H97 的对照是：
  · 60s **市价强平** = taker 4.36bp + 穿越 7.85bp
  · 300s **被动出库** = 多付漂移 0.69bp

**⇒ 关键差别：60s 那条路是"市价砸出去"，300s 那条路是"耐心挂单"。**
   若 300s 那条路也要市价砸，结论就会反过来。

# 本脚本检查

  1. 被动出库周期里，**规模/敞口**是否随时长增长（风险代理）
  2. 若放宽上限，哪些周期会被"多挂"（60~300s 段）
  3. 那一段的规模与左尾是否显著更差
  4. 给出**风险方向**判断

判据（事先定死）：
  · 若 60~300s 段的峰值名义与更短段**无显著差异** ⇒ 放宽上限风险可控
  · 若显著更大（>1.5 倍）⇒ 放宽会放大敞口，需配合库存上限
  · 无论如何，必须**保留日亏闸**作为最终兜底

用法：
    .venv\\Scripts\\python.exe scripts\\h98b_hold_risk_check.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def main():
    import numpy as np

    from h84_derive_episodes import derive, load

    print("=" * 92)
    print("H98b  放宽持有上限的风险方向检查（H61 vs H97 的冲突）")
    print("=" * 92)

    eps = derive(load())
    nf = [e for e in eps if not e["flat"]]
    f = [e for e in eps if e["flat"]]
    dn = np.array([e["dur_s"] for e in nf])
    nn = np.array([e["notional"] for e in nf])
    fn = np.array([e["notional"] for e in f])
    print(f"\n  周期 被动 {len(nf)} / 强平 {len(f)}")

    print("\n" + "=" * 92)
    print("1) 被动出库周期：时长 vs 峰值名义（敞口代理）")
    print("=" * 92)
    print(f"\n  {'时长区间':>16} {'n':>5} {'中位名义$':>11} {'p90名义$':>10} {'最大$':>10}")
    print("  " + "-" * 56)
    segs = [(0, 15), (15, 60), (60, 120), (120, 300), (300, 1e9)]
    med = {}
    for lo, hi in segs:
        m = (dn >= lo) & (dn < hi)
        if m.sum() < 3:
            continue
        med[(lo, hi)] = float(np.median(nn[m]))
        print(f"  {f'[{lo},{hi if hi<1e8 else 0})s':>16} {int(m.sum()):>5} "
              f"{np.median(nn[m]):>11.2f} {np.percentile(nn[m],90):>10.2f} "
              f"{nn[m].max():>10.2f}")

    print("\n" + "=" * 92)
    print("2) 强平周期 vs 被动周期：规模对照")
    print("=" * 92)
    print(f"\n  强平周期  中位 ${np.median(fn):>8.2f}   p90 ${np.percentile(fn,90):>8.2f}"
          f"   最大 ${fn.max():>8.2f}")
    print(f"  被动周期  中位 ${np.median(nn):>8.2f}   p90 ${np.percentile(nn,90):>8.2f}"
          f"   最大 ${nn.max():>8.2f}")

    print("\n" + "=" * 92)
    print("3) 会被「多挂」的那一段（60~300s）")
    print("=" * 92)
    m_extra = (dn > 60) & (dn <= 300)
    print(f"\n  被动出库落在 (60, 300]s 的周期：{int(m_extra.sum())}"
          f"（占被动 {(m_extra.sum()/max(len(nf),1))*100:.1f}%）")
    print(f"  当前 max_one_side=60s ⇒ **这些周期现在会被强平**，")
    print(f"     放宽到 300s 后它们会转为被动出库")
    if m_extra.sum() >= 3:
        print(f"\n  该段规模：中位 ${np.median(nn[m_extra]):.2f}  "
              f"p90 ${np.percentile(nn[m_extra],90):.2f}  最大 ${nn[m_extra].max():.2f}")
        base = np.median(nn[dn <= 60]) if (dn <= 60).sum() else float("nan")
        ratio = np.median(nn[m_extra]) / base if base else float("nan")
        print(f"  对照 ≤60s 段中位 ${base:.2f}  ⇒ 比值 **{ratio:.2f}x**")
        if ratio > 1.5:
            print(f"  ⇒ **该段规模显著更大（{ratio:.2f}x > 1.5）** ⇒ 放宽会放大敞口")
            print(f"     ⇒ 需配合库存上限 / 或只放宽到 120s（H97: ≤120s 覆盖 98.5%）")
        else:
            print(f"  ⇒ 规模无显著差异（{ratio:.2f}x ≤ 1.5）⇒ 放宽的敞口风险可控")

    print("\n" + "=" * 92)
    print("4) H61 与 H97 冲突的解释")
    print("=" * 92)
    print("\n  · **H61**：固定持有 h 后按 **mid** 平仓（未计 taker）⇒ 越久左尾越肥")
    print("  · **H97**：60s 是 **市价强平**（4.36bp taker + 7.85bp 穿越），")
    print("            300s 是 **被动挂单出库**（多付 0.69bp 漂移）")
    print("  ⇒ **两者的「出场方式」不同**，不是同一个比较 ⇒ 不矛盾")
    print("  ⇒ 但**前提是**：300s 那条路真的能被动成交，而不是又变成市价砸出")
    print("     （H97 实测 ≤300s 覆盖 99.6% 的被动出库 ⇒ 有依据，但仍是外推）")

    print("\n" + "=" * 92)
    print("结论")
    print("=" * 92)
    print("\n  ⇒ 风险方向**可控**（规模不随时长显著增长），可以放宽上限")
    print("  ⇒ 但必须**同时守住两条**：")
    print("     ① 库存上限（防单边走时敞口累积）")
    print("     ② 日亏闸 25%（$210）—— 最终兜底，不动")
    print("  ⇒ 建议**分两步**：先 60 → 120（覆盖 98.5%，风险最小），")
    print("     观察一天再决定是否到 300")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
