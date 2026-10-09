"""H70：AI 选币的评分函数是不是把顺序搞反了？

`backend/services/coin_select_hft.py:254`：

    base = st.spread_med_bp * math.log10(throughput)

**点差是成本，不是收益。** 对做市而言：
  · 价差越宽 ⇒ 每笔毛捕获越多（好处，**线性**）
  · 价差越宽通常意味着流动性越差 ⇒ 成交笔数越少（坏处，**也是线性或更陡**）

**净收入 ≈ (spread_mult × 半价差) × 成交笔数 − 逆选择成本**

⇒ 一个只看 `spread × log10(吞吐)` 的分数，在"宽价差 + 极低吞吐"的币上会给高分，
**但那种币每小时的绝对收入可能极低**。用户报的"完全没有交易"正是这个症状。

本脚本：用**实际数据**算两个排序，看是否反了
  排序 A = 当前评分 `spread_med_bp × log10(吞吐)`
  排序 B = 单位时间毛收入代理 `spread_med_bp × trades_per_hour`
并给出两者的秩相关。

判据：
  · 秩相关为负或接近 0 ⇒ **当前评分不能代表收入**，选币结果需要修正
  · 秩相关高 ⇒ 评分没问题，问题只在"没接到车道上"

用法：
    .venv\\Scripts\\python.exe scripts\\h70_score_sanity.py
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
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env", override=False)


def main():
    import numpy as np

    print("=" * 104)
    print("H70  AI 选币评分函数 sanity check —— spread × log10(吞吐)")
    print("=" * 104)

    from backend.services.coin_select_hft import select_universe, _load_stats

    # 直接拿底层统计（含 trades_total / book_updates），不要只拿 scored 的摘要
    try:
        stats = _load_stats(force=True)
    except TypeError:
        stats = _load_stats()
    if not stats:
        print("拿不到 stats")
        return 1

    rows = []
    for sym, st in stats.items():
        sp = getattr(st, "spread_med_bp", None)
        tr = getattr(st, "trades_total", 0) or 0
        bu = getattr(st, "book_updates", 0) or 0
        if sp is None:
            continue
        rows.append({"sym": sym, "spread": float(sp), "trades": int(tr),
                     "book": int(bu)})
    if not rows:
        print("无有效统计")
        return 1

    import math
    print(f"\n  样本 {len(rows)} 币")
    print(f"\n  {'币':<12} {'价差bp':>9} {'trades':>9} {'book_upd':>10} "
          f"{'当前评分':>10} {'spread×trades':>14} {'排名A':>6} {'排名B':>6}")
    print("  " + "-" * 88)

    for r in rows:
        thru = max(1e-9, math.log10(max(r["book"], 1) + max(r["trades"], 1) + 1))
        r["scoreA"] = r["spread"] * thru            # 当前口径
        r["scoreB"] = r["spread"] * r["trades"]     # 单位时间毛收入代理

    byA = sorted(rows, key=lambda x: -x["scoreA"])
    byB = sorted(rows, key=lambda x: -x["scoreB"])
    rankA = {r["sym"]: i + 1 for i, r in enumerate(byA)}
    rankB = {r["sym"]: i + 1 for i, r in enumerate(byB)}
    for r in sorted(rows, key=lambda x: rankA[x["sym"]]):
        print(f"  {r['sym']:<12} {r['spread']:>9.3f} {r['trades']:>9,} {r['book']:>10,} "
              f"{r['scoreA']:>10.2f} {r['scoreB']:>14,.0f} "
              f"{rankA[r['sym']]:>6} {rankB[r['sym']]:>6}")

    # 秩相关
    a = np.array([rankA[r["sym"]] for r in rows], dtype=float)
    b = np.array([rankB[r["sym"]] for r in rows], dtype=float)
    rho = float(np.corrcoef(a, b)[0, 1]) if a.std() > 0 and b.std() > 0 else float("nan")

    print("\n" + "=" * 104)
    print(f"  两种排序的秩相关（Pearson on ranks）= **{rho:+.3f}**")
    print("=" * 104)
    topA = [r["sym"] for r in byA[:5]]
    topB = [r["sym"] for r in byB[:5]]
    print(f"\n  当前评分 Top5 : {topA}")
    print(f"  毛收入代理 Top5: {topB}")
    print(f"  重合: {sorted(set(topA) & set(topB))}")

    if rho < 0.3:
        print(f"\n  ⇒ **两种排序基本不一致（rho={rho:+.3f}）**")
        print("     ⇒ 当前评分**不能代表单位时间收入** ⇒ 选币结果需要修正")
        print("     ⇒ 用户报的「完全没有交易」与此一致：高分币是宽价差但几乎没成交的币")
    elif rho < 0.7:
        print(f"\n  ⇒ 部分一致（rho={rho:+.3f}）⇒ 评分有参考性但不精确")
    else:
        print(f"\n  ⇒ 高度一致（rho={rho:+.3f}）⇒ 评分没问题")

    # 毛收入代理的绝对量级（用半价差 × spread_mult=0.9 估）
    print("\n" + "=" * 104)
    print("  单位时间毛收入代理（bp × 笔数；再乘 0.9 半价差比例与 $140 腿量 → USD/窗口）")
    print("=" * 104)
    print(f"\n  {'币':<12} {'spread×trades':>14} {'× 0.45bp 捕获':>15} {'@ $140 腿':>12}")
    print("  " + "-" * 58)
    for r in byB[:10]:
        gross_bp = r["spread"] * 0.45     # spread_mult 0.9 × 半价差 = 0.45 × spread
        usd = gross_bp / 1e4 * 140.0 * r["trades"]
        print(f"  {r['sym']:<12} {r['scoreB']:>14,.0f} {gross_bp*r['trades']:>15,.0f} "
              f"{usd:>12,.2f}")
    print("\n  （这是**毛捕获**，未扣逆选择与费用；正负要看实盘边际）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
