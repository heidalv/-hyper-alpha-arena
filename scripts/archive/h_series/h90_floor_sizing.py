"""H90：F288 下限的取值分析 + 各币影响预估（部署前）。

# 数学关系（先讲清，避免拍数）

引擎：`edge_bp = spread_mult × 半价差`（当前 `spread_mult=0.9`）
F288 下限：`edge_bp ≥ min_edge_frac × 半价差`

两者都是"半价差的倍数" ⇒ **下限只在 `min_edge_frac > spread_mult` 时才生效**，
生效时把报价宽度从 `0.9h` 抬到 `F·h`。

等价表述：**把"有效价差"的下限抬到 `2F / 0.9` bp**。

# H88 的五档（实盘 561 周期）

| edge_bp 区间 | 周期 | 强平率 | 每周期净额 USD |
|---|---|---|---|
| (-inf, 0.1071) | 112 | 64.3% | **−0.08610** |
| [0.1071, 0.3202) | 109 | 10.1% | **+0.00410** |
| [0.3202, 0.5120) | 115 | 8.7% | −0.00039 |
| [0.5120, 0.6413) | 111 | 11.7% | −0.01498 |
| [0.6413, inf) | 114 | 25.4% | −0.03165 |

**注意这是"内部最优"**：最窄档最差，但最宽档也不好 ⇒ 只能"抬地板"，不能整体放宽。
最优档是 `[0.107, 0.320)`。

# 本脚本输出

  1. 各 `F` 对应的"最小有效价差"
  2. 用车道 11 个币的**真实价差**估算：每个币当前落在哪一档、F 生效后被抬到哪一档
  3. 给出建议值与其代价（被抬的币会少成交）

用法：
    .venv\\Scripts\\python.exe scripts\\h90_floor_sizing.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

SPREAD_MULT = 0.9
# 车道 11 个币的价差 p50（H55 实测，asterdex_book_ticker 近 2h）
SPREADS = {
    "SOL": 0.9245, "DOGE": 1.1745, "ASTER": 1.3705, "XRP": 1.4450,
    "UNI": 5.7954, "ONDO": 9.7585, "ARB": 10.5711, "1000SHIB": 14.8176,
    "SEI": 18.9374, "PENDLE": 19.5503, "VIRTUAL": 20.1942,
}
# H88 五档（下界, 强平率, 每周期净额）
BUCKETS = [(-1e9, 0.1071, 0.643, -0.08610),
           (0.1071, 0.3202, 0.101, +0.00410),
           (0.3202, 0.5120, 0.087, -0.00039),
           (0.5120, 0.6413, 0.117, -0.01498),
           (0.6413, 1e9, 0.254, -0.03165)]


def bucket_of(edge):
    for lo, hi, fr, pnl in BUCKETS:
        if lo <= edge < hi:
            return lo, hi, fr, pnl
    return BUCKETS[-1]


def main():
    print("=" * 96)
    print("H90  F288 下限取值分析（部署前预估，不实盘）")
    print("=" * 96)

    print("\n【1】F 与「最小有效价差」的对应关系")
    print(f"    引擎 spread_mult = {SPREAD_MULT}；下限仅在 F > {SPREAD_MULT} 时生效")
    print(f"\n  {'F':>6} {'最小有效价差bp':>16} {'说明':<30}")
    print("  " + "-" * 56)
    for F in (0.9, 1.0, 1.1, 1.2, 1.3, 1.4, 1.5, 2.0):
        minsp = 2 * F / SPREAD_MULT
        if abs(F - 0.9) < 1e-9:
            note = "= 当前行为（下限不生效）"
        elif minsp <= 1.5:
            note = "温和：只抬最窄的几个币"
        elif minsp <= 3.0:
            note = "中等"
        else:
            note = "激进：显著减少窄价差币成交"
        print(f"  {F:>6.1f} {minsp:>16.2f} {note:<30}")

    print("\n" + "=" * 96)
    print("【2】各币当前档位 vs 下限生效后的档位")
    print("=" * 96)
    for F in (0.9, 1.2, 1.5):
        print(f"\n  ── F = {F} ──")
        print(f"  {'币':<10} {'价差bp':>9} {'当前edge':>10} {'当前档':>14} "
              f"{'抬后edge':>10} {'抬后档':>14} {'被抬?':>7}")
        print("  " + "-" * 82)
        n_lift = 0
        for s, sp in sorted(SPREADS.items(), key=lambda x: x[1]):
            h = sp / 2.0
            e_now = SPREAD_MULT * h
            e_new = max(e_now, F * h)
            b_now = bucket_of(e_now)
            b_new = bucket_of(e_new)
            lifted = "是" if e_new > e_now + 1e-12 else ""
            if lifted:
                n_lift += 1
            print(f"  {s:<10} {sp:>9.4f} {e_now:>10.4f} "
                  f"{f'[{b_now[0]:.3f},{b_now[1]:.3f})':>14} {e_new:>10.4f} "
                  f"{f'[{b_new[0]:.3f},{b_new[1]:.3f})':>14} {lifted:>7}")
        print(f"\n  ⇒ F={F} 会抬升 {n_lift}/{len(SPREADS)} 个币的报价")

    print("\n" + "=" * 96)
    print("【3】建议")
    print("=" * 96)
    print("  · H88 最优档是 [0.107, 0.320) ⇒ 下限只需把 edge 抬进该区间下沿即可")
    print(f"  · `F=1.0`（= 最小有效价差 2.2bp）把 edge 恰好抬到半价差本身，")
    print(f"    对应 H88 的 [0.320, 0.512) 档（强平率 8.7%，净额 −0.0004）")
    print(f"  · 但注意：**抬地板会让窄价差币少成交**，总收益未必提高")
    print(f"    ⇒ 先上 **F=1.2**（最小有效价差 2.7bp）做 A/B，再用周期口径实测")
    print(f"\n  ⚠️ H88/H89 是**观测**证据。要定因果必须随机化 A/B。")
    print(f"     上线的同时应记录：哪些币被抬、强平率与每周期净额如何随之变化。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
