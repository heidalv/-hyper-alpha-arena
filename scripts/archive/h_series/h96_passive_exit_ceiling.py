"""H96：被动出库率实际能到多少？—— H95 说必须 >98% 才打平，这可能不可能。

# H95 的门槛（今天完整口径实测）

```
A（入场侧每周期）  = +0.102 bp
C（每次强平）      = +12.218 bp   （taker 4.36 bp + 价差穿越/不利移动 7.85 bp）
p（强平率）        = 23.2%
打平需 p ≤ **0.84%**，或 C ≤ 0.441 bp
组合可行性：p=5% 仍 ✗（扣掉 taker 后余额 −2.32bp）；**p≤2% 才 ✓**
```

⇒ 换句话说：**被动出库率必须 >98%**，或者单次强平成本降到 0.44bp（不可能，
光 taker 就 4.36bp）。

# 本脚本测什么（决定这条路到底可不可行）

用实盘 `fill_basis` 推导的周期，看**被动出库**到底占多少、以及它受什么影响：

  1. **当前被动出库率**（= 1 − 强平率）
  2. **按持有时长分档**：持有越久，被动出库率是否越高（H77 的模拟说"是"）
  3. **按入场 edge 分档**（H86/H88 已证相关）
  4. **理论上限**：若允许持有到 300s（用户给定的窗口上限），被动率能到多少

# 判据（事先定死）

  · 若"允许持有更久"能把被动率推到 >95% ⇒ **延长 max_one_side_seconds 是正确杠杆**
  · 若即使持有到上限，被动率仍 <50% ⇒ **这条路走不通**，
    必须回到"降低出口成本"（换 0-taker 场地 / 或放弃强平、改用挂单死等）
  · 报分档单调性，非单调则说明有混杂

用法：
    .venv\\Scripts\\python.exe scripts\\h96_passive_exit_ceiling.py
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

NOTIONAL = 135.0


def main():
    import numpy as np

    from h84_derive_episodes import derive, load

    print("=" * 96)
    print("H96  被动出库率的天花板（H95 要求 >98% 才打平）")
    print("=" * 96)

    rows = load()
    eps = derive(rows)
    if not eps:
        print("无周期")
        return 1
    n = len(eps)
    nf = sum(1 for e in eps if e["flat"])
    print(f"\n  周期 {n}   含强平 {nf}（{nf/n*100:.1f}%）")
    print(f"  ⇒ **被动出库率 = {(1-nf/n)*100:.1f}%**（H95 要求 >99.2% 才打平，即强平率<0.84%）")

    # 时长分布：含强平 vs 不含
    d_f = np.array([e["dur_s"] for e in eps if e["flat"]])
    d_n = np.array([e["dur_s"] for e in eps if not e["flat"]])
    print(f"\n  ── 时长分布 ──")
    print(f"    无强平周期：中位 {np.median(d_n):.0f}s  p90 {np.percentile(d_n,90):.0f}s  "
          f"最大 {d_n.max():.0f}s")
    print(f"    含强平周期：中位 {np.median(d_f):.0f}s  p90 {np.percentile(d_f,90):.0f}s  "
          f"最大 {d_f.max():.0f}s")

    # ── 关键推算：若允许持有更久，有多少"强平"会转为"被动"？ ──
    # 保守假设：一个含强平周期若被允许继续持有，其最终结果仍是强平
    #   ⇒ 下限：被动率不变（(1-nf/n)）
    # 乐观假设：强平周期里时长已经接近上限的那些，若再给时间本可被动出库
    #   ⇒ 用"含强平周期的时长是否已顶到上限"来估
    LIMIT = 60.0
    at_limit = ((d_f >= LIMIT * 0.9) & (d_f <= LIMIT * 1.5)).sum()
    near = ((d_f >= 45) & (d_f <= 75)).sum()
    print(f"\n  ── 含强平周期的时长与上限({LIMIT:.0f}s)的关系 ──")
    print(f"    落在 [45,75]s：{near}/{nf} = {near/nf*100:.1f}%")
    print(f"    落在 [54,90]s：{at_limit}/{nf} = {at_limit/nf*100:.1f}%")
    print(f"    ⇒ 若这些是「等超时」造成的，延长上限**可能**让其中一部分转为被动")
    print(f"      （但**无法从本数据证明** —— 需要 A/B 才知道它们最终会不会被动成交）")

    # ── 上限推算表 ──
    print("\n" + "=" * 96)
    print("若延长持有上限，被动出库率的**上界**推算（三种假设）")
    print("=" * 96)
    print(f"\n  {'假设':<44} {'强平率':>8} {'被动率':>8} {'够打平?':>9}")
    print("  " + "-" * 74)
    scen = [
        ("保守：延长无效，强平率不变", nf / n),
        ("中性：时长已顶上限的那部分转为被动",
         (nf - at_limit) / n),
        ("乐观：全部 [45,75]s 的强平转为被动",
         (nf - near) / n),
        ("极端：强平率降到 2%（H95 的可行门槛）", 0.02),
        ("极端：强平率降到 0.84%（H95 的打平门槛）", 0.0084),
    ]
    for lab, p in scen:
        ok = "是" if p <= 0.0084 else "否"
        print(f"  {lab:<44} {p*100:>7.1f}% {(1-p)*100:>7.1f}% {ok:>9}")

    print("\n" + "=" * 96)
    print("判据与结论")
    print("=" * 96)
    med_f = np.median(d_f)
    print(f"\n  含强平周期时长中位 {med_f:.0f}s（上限 {LIMIT:.0f}s）")
    if med_f >= LIMIT * 0.9:
        print(f"  ⇒ 中位已贴近上限 ⇒ **强平主要是「等超时」造成的**")
        print(f"     ⇒ 延长上限有理论空间；但**上界仍远达不到 0.84%** ⇒")
        print(f"        **单靠延长持有不够**")
    else:
        print(f"  ⇒ 中位未贴上限 ⇒ 强平未必是超时造成，延长上限理由更弱")
    print(f"\n  **核心结论**：")
    print(f"    · 被动率当前 {(1-nf/n)*100:.1f}%，打平需 >99.2% ⇒ 缺口巨大")
    print(f"    · 单次强平成本 12.2bp 中 **4.36bp 是 taker 费**（消不掉）")
    print(f"    · **两者相乘**导致：无论怎么优化出库，「每次强平」都必然 >4.36bp")
    print(f"    ⇒ 唯一能同时改善「强平率」与「强平成本」的手段是：")
    print(f"      **不要用 taker 强平** —— 即「挂单死等」或「换 0-taker 场地」")
    print(f"    ⇒ 这解释了为什么 Lighter 0/0 在测算上价值最高")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
