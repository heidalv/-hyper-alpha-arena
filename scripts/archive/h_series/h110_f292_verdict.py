"""H110：F292 该留还是该回滚？—— 用**同上限、同市场期**的对照，避免循环论证。

# 为什么必须单独做这个

H106 的分期看起来是：
```
① 60s 上限（03:20 前）   周期 634   强平率 23.2%
② F291 120s（03:20-34）  周期  58   强平率 10.3%
③ F292 出库0.30（03:34后）周期 1411  强平率 13.0%
```

**但②只有 58 个周期（14 分钟），③有 1411 个（5.5 小时）**
⇒ 拿薄样本的最优值去否定厚样本，是错的。

# ⚠️ 一个必须先排除的循环论证

我本想用"时长 vs 强平率"来决定 `max_one_side_seconds`：
```
   0-  15s  强平率 12.8%      60- 120s  强平率 21.1%
  15-  30s  强平率  0.5%     120- 300s  强平率 **61.6%**
  30-  60s  强平率  2.9%     300s+     强平率 **78.6%**
```
**但这个关系是反向因果**：被强平的周期**因为被强平**才活到 120~300s
（`max_one_side_seconds` 到了才强平）⇒ **不能用它推"短持有更安全"** ✗

# 本脚本的正确对照

`max_one_side_seconds` **都是 120s**（03:20 之后），只有出库倍数不同：
  · **A 组** = F291 期间（03:20-03:34），`spread_mult_reduce = 0.95`
  · **B 组** = F292 之后（03:34 后），`spread_mult_reduce = 0.30`

**两组同上限 ⇒ 时长不再是被强平的结果**（上限相同，不会因为组别不同而改变行为）
⇒ 可以直接比强平率。

判据（事先定死）：
  · B 的强平率 < A ⇒ F292 有效 ⇒ 保留，甚至推到 0.00
  · B 的强平率 > A ⇒ **F292 无效/有害 ⇒ 回滚到 0.95**
  · 任一组的周期数 < 100 ⇒ 只报方向

用法：
    .venv\\Scripts\\python.exe scripts\\h110_f292_verdict.py
"""
from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import datetime

F291 = datetime.datetime(2026, 9, 21, 3, 20).timestamp()
F292 = datetime.datetime(2026, 9, 21, 3, 34).timestamp()


def main():
    import numpy as np

    from h84_derive_episodes import derive, load

    print("=" * 100)
    print("H110  F292 该留还是该回滚？（同上限 120s 下的对照）")
    print("=" * 100)

    eps = derive(load())
    groups = {
        "A 组：F291（出库 0.95）": (F291, F292),
        "B 组：F292（出库 0.30）": (F292, 9e18),
    }
    print(f"\n  {'组':<26} {'周期':>6} {'含强平':>7} {'强平率':>9} "
          f"{'时长中位':>9} {'时长p90':>9}")
    print("  " + "-" * 70)
    res = {}
    for lab, (lo, hi) in groups.items():
        sub = [e for e in eps if lo <= (e.get("ts0") or 0) < hi]
        if not sub:
            print(f"  {lab:<26} 无周期")
            continue
        f = sum(1 for e in sub if e["flat"])
        d = np.array([e["dur_s"] for e in sub])
        res[lab] = {"n": len(sub), "f": f, "p": f / len(sub),
                    "dmed": float(np.median(d)), "dp90": float(np.percentile(d, 90))}
        print(f"  {lab:<26} {len(sub):>6} {f:>7} {f/len(sub)*100:>8.1f}% "
              f"{np.median(d):>8.0f}s {np.percentile(d,90):>8.0f}s")

    print("\n" + "=" * 100)
    print("判据")
    print("=" * 100)
    if len(res) < 2:
        print("  样本不足，无法对照")
        return 0
    a = res["A 组：F291（出库 0.95）"]
    b = res["B 组：F292（出库 0.30）"]
    print(f"\n  A（0.95）强平率 {a['p']*100:.1f}%  n={a['n']}")
    print(f"  B（0.30）强平率 {b['p']*100:.1f}%  n={b['n']}")
    # 两比例之差的近似标准误
    se = np.sqrt(a["p"] * (1 - a["p"]) / a["n"] + b["p"] * (1 - b["p"]) / b["n"])
    diff = b["p"] - a["p"]
    print(f"\n  差 = {diff*100:+.1f} pp   合并标准误 {se*100:.1f} pp   "
          f"比值 {abs(diff)/max(se,1e-12):.2f}")
    if a["n"] < 100:
        print(f"\n  ⚠️ A 组仅 {a['n']} 个周期（<100）⇒ **只报方向，不构成判定**")
    if diff < -2 * se:
        print("  ⇒ B 显著更低 ⇒ **F292 有效** ⇒ 保留，可考虑推到 0.00")
    elif diff > 2 * se:
        print("  ⇒ B 显著更高 ⇒ **F292 有害 ⇒ 回滚到 0.95**")
    else:
        print("  ⇒ 差异在噪声内 ⇒ **无法判定**")

    print("\n" + "=" * 100)
    print("⚠️ 与 H104 的冲突（必须写清）")
    print("=" * 100)
    print("  H104 用**模拟**（主动买最高价 ≥ 我们卖价）说 f=0 成交率最高（84.5% vs 79.3%）")
    print("  但那是**上界**，假设'排到队首且主动买一定吃到我们'。")
    print("  实盘 B 组（f=0.30）没有改善 ⇒ 可能是：")
    print("    ① f 的实际作用被其他机制掩盖（如撤单重挂丢失队列位置）")
    print("    ② H104 的'上界'假设在实盘不成立（我们**不是**队首）")
    print("    ③ 市场期不同（B 组跨了 5.5 小时，含不同行情）")
    print("  ⇒ **实盘优先于模拟**：若实盘无改善，就以实盘为准。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
