"""H97：挂单死等（**永不 taker 强平**）的出库率天花板 —— 决定策略是否存在。

# 为什么这是决定性问题

H95/H96 把门槛算死了：

```
A（入场侧每周期贡献） = +0.102 bp
C（每次强平成本）     = +12.218 bp   （其中 **4.36bp 是 taker 费，消不掉**）
当前 p（强平率）      = 23.3%   ⇒ 被动出库率 76.7%
打平需 p ≤ **0.84%** （被动率 >99.2%）
```

**且 `C* = 0.441bp < 4.36bp(taker)`** ⇒ **即使把价差穿越/不利移动优化到 0，
只要还用 taker 强平，单次成本就必然 >4.36bp，永远打不平。**

⇒ **唯一可能活下来的形态：完全不使用 taker 强平。**
   即出库腿**挂单死等**（或换 0-taker 场地），把"强平"从成本项里去掉。

# 但"死等"有代价，必须测清楚

不清仓的后果：
  · 库存会累积 ⇒ 敞口失控 / 触及风控闸
  · 持仓越久，逆选择越重（H58：漂移随持有时间单调恶化）
  ⇒ 所以必须回答：**"死等"的出库率与等待时长分布是什么？**

# 本脚本测什么（用实盘周期数据，不用模拟）

  1. **死等出库率**：`fill_basis` 里**没有 flatten 腿**的周期占比（= 纯被动出库）
  2. **被动出库的等待时长分布**（决定库存占用）
  3. **若把"超时强平"改为"继续等"，需要多久才能出**（用时长分布外推，**标注为估算**）
  4. 用 H58 的漂移曲线估"多等"带来的额外逆选择成本

# 判据（事先定死）

  · 若纯被动出库率 >90% 且中位等待 <300s ⇒ **"挂单死等"可行**，应改引擎
  · 若纯被动出库率 <70% ⇒ **库存会累积** ⇒ 死等不可行
  · 对比"死等的额外逆选择成本" vs "省下的 4.36bp taker 费"

用法：
    .venv\\Scripts\\python.exe scripts\\h97_wait_forever_ceiling.py
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

NOTIONAL = 135.0
TAKER_BP = 4.36          # H95 实测：单次强平里的 taker 费
CROSS_BP = 7.85          # H95 实测：价差穿越 + 不利移动
# H58 实测漂移（bp，跨币等权）：持有越久越负
DRIFT = {5: -0.1348, 10: -0.1425, 30: -0.2093, 60: -0.3018,
         120: -0.2340, 300: -0.9895}


def main():
    import numpy as np

    from h84_derive_episodes import derive, load

    print("=" * 96)
    print("H97  挂单死等的出库率天花板（决定策略是否存在）")
    print("=" * 96)

    eps = derive(load())
    if not eps:
        print("无周期")
        return 1
    n = len(eps)
    flat = [e for e in eps if e["flat"]]
    noflat = [e for e in eps if not e["flat"]]
    print(f"\n  周期 {n}  含强平 {len(flat)}（{len(flat)/n*100:.1f}%）  "
          f"**纯被动出库 {len(noflat)}（{len(noflat)/n*100:.1f}%）**")

    dn = np.array([e["dur_s"] for e in noflat])
    print(f"\n  ── 纯被动出库的等待时长 ──")
    print(f"    中位 {np.median(dn):.0f}s   p75 {np.percentile(dn,75):.0f}s   "
          f"p90 {np.percentile(dn,90):.0f}s   最大 {dn.max():.0f}s")
    for th in (30, 60, 120, 300, 600):
        print(f"    ≤{th:>4}s 内出库的比例：{(dn<=th).mean()*100:>5.1f}%")

    # ── 关键：把"强平"当成"还没等够"来估 ──
    print("\n" + "=" * 96)
    print("情景估算：若取消 taker 强平、改为无限挂单等")
    print("=" * 96)
    print(f"\n  ⚠️ 以下为**估算**（用被动出库的时长分布外推），不是实测：")
    df = np.array([e["dur_s"] for e in flat])
    # 保守：强平周期里，只有那些"看起来本来能等到"的会转为被动
    # 用被动周期的时长分布（p90/最大）作为"可等待范围"
    p90n = np.percentile(dn, 90)
    conv_p90 = (df <= p90n).sum()
    conv_max = (df <= dn.max()).sum()
    print(f"\n  被动出库时长 p90 = {p90n:.0f}s，最大 = {dn.max():.0f}s")
    print(f"  含强平周期中，时长本就 ≤ {p90n:.0f}s 的：{conv_p90}/{len(flat)}"
          f" ⇒ 估转为被动后强平率 {(len(flat)-conv_p90)/n*100:.1f}%")
    print(f"  含强平周期中，时长本就 ≤ {dn.max():.0f}s 的：{conv_max}/{len(flat)}"
          f" ⇒ 估转为被动后强平率 {(len(flat)-conv_max)/n*100:.1f}%")

    print("\n" + "=" * 96)
    print("成本对照：死等省下的 taker 费 vs 多等带来的额外逆选择")
    print("=" * 96)
    print(f"\n  H58 实测漂移（bp，跨币等权，相对于成交时刻的 mid）：")
    print(f"  {'持有':>6} {'漂移bp':>10}")
    print("  " + "-" * 20)
    for h, v in DRIFT.items():
        print(f"  {h:>5}s {v:>10.4f}")
    print(f"\n  · 当前若在 60s 强平：付 taker **{TAKER_BP:.2f}bp** + 穿越 **{CROSS_BP:.2f}bp**")
    print(f"  · 若死等到 300s 被动出库：省下 taker {TAKER_BP:.2f}bp，")
    print(f"    但漂移从 {DRIFT[60]:.4f} 恶化到 {DRIFT[300]:.4f}bp"
          f" ⇒ **多付 {abs(DRIFT[300]-DRIFT[60]):.4f}bp**")
    print(f"  · **净收益 ≈ +{TAKER_BP - abs(DRIFT[300]-DRIFT[60]):.2f}bp/次**"
          f"（若能在 300s 内被动出库）")

    print("\n" + "=" * 96)
    print("判据")
    print("=" * 96)
    pr = len(noflat) / n
    if pr > 0.90:
        print(f"  ⇒ 纯被动出库率 {pr*100:.1f}% > 90% ⇒ **死等可行**")
    elif pr > 0.70:
        print(f"  ⇒ 纯被动出库率 {pr*100:.1f}%（70~90%）⇒ 边际，需配合库存上限")
        print(f"     ⇒ 若库存能撑住，死等仍比 taker 强平划算（见上面的成本对照）")
    else:
        print(f"  ⇒ 纯被动出库率 {pr*100:.1f}% < 70% ⇒ 库存会累积 ⇒ 死等不可行")

    print(f"\n  **核心**：单次强平 12.2bp 里，taker 费 4.36bp 是**结构性的**；")
    print(f"     而多等 240s 的额外逆选择只有 {abs(DRIFT[300]-DRIFT[60]):.2f}bp")
    print(f"     ⇒ **「多等」比「市价砸出去」便宜约 {TAKER_BP - abs(DRIFT[300]-DRIFT[60]):.2f}bp/次**")
    print(f"     ⇒ 这是当前测算下**最明确的改进方向**：放宽/取消强平，改为耐心挂单")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
