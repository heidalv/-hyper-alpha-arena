# -*- coding: utf-8 -*-
"""[H137 2026-09-21] 「makere 免费」的直接推论：被动出库能等多久？

# 用户这句话为什么是全部要点

实测账本（`lane_ledger`，全历史）：

    入场腿（maker） 12,269 笔  平均 fee = **0.0000 bp**   ← 完全免费
    强平腿（taker）    917 笔  平均 fee = **−3.9956 bp**  ← 全部成本在这
    强平腿累计 taker 费 = **−$50.87**

而整夜亏损是 −$52.20 ⇒ **$50.87 / $52.20 = 97% 的亏损就是 taker 费**。

所以目标函数应该是：

    每周期净额 = 入场捕获 − 强平率 × (taker费 + 逆向移动)

**maker 出库 = 不花钱、还赚点差** ⇒ 每把一笔强平转成被动出库，就省下
`4.0bp + 逆向移动`。这不是"优化"，是**直接把主要成本项清零**。

# 本脚本量什么

被动出库的**时间上限**：把持仓放着不管，价格回到开仓价需要多久？
  · 用 H84 推导的周期 + `mm_fill_basis` 逐笔时间戳
  · 对每个周期取**第一次达到可平仓条件**（mid 回到 avg_mid 以内）的时间
  · 给出"等 N 秒内能自然出库"的累积比例

# 判据

  · 若 300s 内自然出库比例已接近 100% ⇒ `max_one_side_seconds` 不是瓶颈，
    问题在**挂单宽度**（挂太宽 ⇒ 对手方不来接）
  · 若 300s 内远不到 100% ⇒ 应该**拉长**持有窗口（H97 已证 60→300s 只多付
    0.69bp 漂移却省 4.36bp taker ⇒ 每笔净赚 +3.67bp）

用法：
    .venv\\Scripts\\python.exe scripts\\h137_passive_exit_ceiling.py
"""
from __future__ import annotations

import json
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from h84_derive_episodes import derive, load  # noqa: E402

BASIS = ROOT / "logs" / "mm_fill_basis.jsonl"
TAKER_FEE_BP = 4.0
ADVERSE_BP = 6.3          # H124 反推：强平成本 ≈10.67bp，其中 taker 4.0bp


def main() -> int:
    rows = load()
    eps = derive(rows)
    print("=" * 100)
    print("H137  被动出库的时间上限（maker 免费 ⇒ 值得等）")
    print("=" * 100)
    print(f"  fill_basis {len(rows):,} 行   周期 {len(eps)}")

    # 只看**未触发强平**的周期 —— 它们是被动出库成功的样本
    clean = [e for e in eps if not e["flat"] and e.get("closed") and e["n"] >= 2]
    forced = [e for e in eps if e["flat"]]
    print(f"\n  被动出库的周期 {len(clean)}   被强平的周期 {len(forced)}")
    if not clean:
        print("  样本不足")
        return 1

    durs = sorted(e["dur_s"] for e in clean)
    print(f"\n  被动出库耗时分布（n={len(durs)}）：")
    for q in (0.5, 0.75, 0.9, 0.95, 0.99):
        idx = min(len(durs) - 1, int(q * len(durs)))
        print(f"    p{int(q*100):<3} {durs[idx]:>8.0f}s")

    print(f"\n  在 N 秒内完成被动出库的比例：")
    print(f"  {'N秒':>6} {'比例':>8} {'未出库':>8}")
    print("  " + "-" * 26)
    for N in (15, 30, 60, 120, 180, 300, 600, 900, 1800):
        k = sum(1 for d in durs if d <= N)
        print(f"  {N:>6} {k/len(durs)*100:>7.1f}% {len(durs)-k:>8}")

    # 强平周期的名义与耗时（看尾部代价）
    if forced:
        fn = [e["notional"] for e in forced if e["notional"] > 0]
        cn = [e["notional"] for e in clean if e["notional"] > 0]
        fd = [e["dur_s"] for e in forced]
        print(f"\n  被强平周期 vs 被动周期：")
        print(f"    峰值名义中位  强平 ${st.median(fn):>8.2f}   被动 ${st.median(cn):>8.2f}"
              f"   （倍数 {st.median(fn)/max(st.median(cn),1e-9):.2f}x）")
        print(f"    时长中位      强平 {st.median(fd):>8.0f}s   被动 {st.median(durs):>8.0f}s")
        print(f"    （注意：『强平周期活得更久』部分是**反向因果** —— 它们是因为被强平才活到上限，")
        print(f"      不能据此说『持有久会导致强平』。见教训 70。）")

    # 经济账：把强平换成被动出库能省多少
    fr = len(forced) / max(len(eps), 1)
    print(f"\n  ── 经济账 ──")
    print(f"    当前强平率 {fr*100:.1f}%")
    print(f"    每次强平成本 = taker {TAKER_FEE_BP}bp + 逆向移动 {ADVERSE_BP}bp"
          f" = **{TAKER_FEE_BP+ADVERSE_BP}bp**")
    print(f"    maker 出库成本 = **0bp**（还是赚点差）")
    print(f"    ⇒ 把强平率压到 X% 时，每周期省 = ({fr*100:.1f}% − X%) × {TAKER_FEE_BP+ADVERSE_BP}bp / 100")
    print(f"\n  {'目标强平率':>10} {'每周期省bp':>12} {'相对当前':>10}")
    print("  " + "-" * 36)
    for tgt in (0.10, 0.07, 0.05, 0.03, 0.01):
        save = (fr - tgt) * (TAKER_FEE_BP + ADVERSE_BP)
        print(f"  {tgt*100:>9.0f}% {save:>+12.4f} {'(基准)' if abs(tgt-fr)<1e-9 else ''}")
    print(f"\n  参考：ASTER 实测每周期净额 +0.6047bp（当前强平率 6.6%）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
