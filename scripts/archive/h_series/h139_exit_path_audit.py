# -*- coding: utf-8 -*-
"""[H139 2026-09-21] 出库路径审计 —— 找出「不该平的平了」是哪道闸干的。

# 目标

用户要求：**出库只在真·价格不利时才 taker**，其余交给被动挂单（maker 免费）。
血证（H138）：917 笔强平里 `price_bp ≤ −40bp` 的只占 **10.9%**，
中位只走了 **−7.39bp** ⇒ 近 90% 是在行情几乎没动时就 taker 出场，白付 4bp。

# 引擎里能发起 taker 出库的路径（全部列出，逐个判定）

读 `backend/services/market_maker/runner.py` 的 `plan_symbol`：

  P1 `stop_loss`（价格止损）—— `should_stop_loss(qty, avg_mid, mid, _sl_bp)`
     ⇒ **这是我们要保留的**（真·价格不利）
  P2 `max_one_side`（持有超时 taker）—— 超过 `max_one_side_seconds` 就 taker
     ⇒ H137 证 300s 不是瓶颈（被动 100% 在 300s 内完成）⇒ 但它仍会在
       "价格没回来"时 taker。**这是主要嫌疑。**
  P3 `trend_pause_bp` / `side_trend_min_bp`（趋势闸）—— 挡**加仓侧**
     ⇒ 不产生 taker，只停报价
  P4 `ofi_toxic_*`（毒性流）—— 同上，挡加仓
  P5 孤儿强平 / 收缩强平 —— 一次性事件

# 判据

  · 若 P2 贡献了大部分强平 ⇒ 应把 P2 改成"**只撤单不 taker**"，
    让仓位挂着等被动成交（maker 免费），仅 P1 保留 taker 权
  · 若 P1 贡献大部分 ⇒ 40bp 阈值需要上调（把"小逆向"也当止损了）

# 本脚本怎么判

账本 `skip` 字段不可用（全 NULL，H138 实测）⇒ 用**间接证据**：
  · 强平腿的 `price_bp` 分布：P1 触发时应有 price_bp ≈ −40bp 的**堆叠**
  · 强平发生时距该币上次成交的间隔：P2 触发时应有 300s 附近的堆叠

用法：
    .venv\\Scripts\\python.exe scripts\\h139_exit_path_audit.py
"""
from __future__ import annotations

import statistics as st
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import json  # noqa: E402


def main() -> int:
    basis = ROOT / "logs" / "mm_fill_basis.jsonl"
    rows = []
    for line in basis.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            pass
    rows.sort(key=lambda r: r.get("ts") or 0)

    print("=" * 100)
    print("H139  出库路径审计（找「不该平的平了」是哪道闸）")
    print("=" * 100)

    # 每个币的成交时间线，用于算"距上次成交的间隔"
    by_sym = defaultdict(list)
    for r in rows:
        by_sym[r.get("symbol")].append(r)

    flat_rows = [r for r in rows if r.get("flatten")]
    print(f"  fill_basis 行 {len(rows):,}   其中 flatten 腿 **{len(flat_rows)}** 笔")
    if not flat_rows:
        print("  没有强平腿样本")
        return 1

    # ── 距上次成交的间隔（P2 超时触发的指纹）────────────────────
    gaps = []
    for r in flat_rows:
        sym = r.get("symbol")
        ts = r.get("ts") or 0.0
        prev = [x.get("ts") for x in by_sym[sym] if (x.get("ts") or 0) < ts]
        if prev:
            gaps.append(ts - max(prev))
    if gaps:
        gaps.sort()
        print(f"\n  【P2 指纹】强平腿距**同币上次成交**的间隔（n={len(gaps)}）：")
        for q in (0.1, 0.25, 0.5, 0.75, 0.9):
            i = min(len(gaps) - 1, int(q * len(gaps)))
            print(f"      p{int(q*100):<3} {gaps[i]:>8.1f}s")
        for lo, hi, lab in ((0, 16, "≤16s（下一拍就平，像止损）"),
                            (16, 60, "16~60s"),
                            (60, 200, "60~200s"),
                            (200, 400, "200~400s（**像 300s 超时**）"),
                            (400, 10**9, ">400s")):
            k = sum(1 for g in gaps if lo < g <= hi)
            print(f"      {lab:<28} {k:>5}  {k/len(gaps)*100:>5.1f}%")

    # ── 动态：强平腿的 price_bp 与间隔的联合分布 ─────────────────
    print(f"\n  【P1 vs P2 判别】按间隔分档看 price_bp（行情漂移）：")
    print(f"      {'间隔档':<16} {'笔数':>6} {'price_bp中位':>13} {'≤-40bp占比':>11}")
    print("      " + "-" * 52)
    buckets = defaultdict(list)
    buck_pb = defaultdict(list)
    for r, g in zip(flat_rows, ([] if not gaps else None) or []):
        pass
    # 重新配对（上面 zip 只是为了占位，下面严谨重算）
    pairs = []
    for r in flat_rows:
        sym = r.get("symbol")
        ts = r.get("ts") or 0.0
        prev = [x.get("ts") for x in by_sym[sym] if (x.get("ts") or 0) < ts]
        g = (ts - max(prev)) if prev else None
        pairs.append((r, g))
    for r, g in pairs:
        if g is None:
            continue
        lab = ("≤16s" if g <= 16 else "16~60s" if g <= 60 else
               "60~200s" if g <= 200 else "200~400s" if g <= 400 else ">400s")
        buck_pb[lab].append(float(r.get("edge_bp") or 0.0))
    # price_bp 不在 fill_basis 里，用 (engine_mid - fill_px) 方向修正近似
    for lab in ("≤16s", "16~60s", "60~200s", "200~400s", ">400s"):
        rs = [r for r, g in pairs if g is not None and (
            "≤16s" if g <= 16 else "16~60s" if g <= 60 else
            "60~200s" if g <= 200 else "200~400s" if g <= 400 else ">400s") == lab]
        if not rs:
            print(f"      {lab:<16} {0:>6}")
            continue
        pb = []
        for r in rs:
            px = float(r.get("fill_px") or 0.0)
            mid = float(r.get("engine_mid") or 0.0)
            if px > 0 and mid > 0:
                sgn = 1.0 if str(r.get("side")).lower() == "buy" else -1.0
                pb.append(sgn * (mid - px) / mid * 1e4)
        if not pb:
            continue
        le40 = sum(1 for x in pb if x <= -40) / len(pb) * 100
        print(f"      {lab:<16} {len(rs):>6} {st.median(pb):>+13.2f} {le40:>10.1f}%")

    # ── 汇总判定 ─────────────────────────────────────────────
    print(f"\n  {'='*96}\n  判定\n  {'='*96}")
    if gaps:
        k300 = sum(1 for g in gaps if 200 < g <= 400)
        kfast = sum(1 for g in gaps if g <= 16)
        print(f"    ≤16s 就强平（像价格止损）      {kfast:>5}  {kfast/len(gaps)*100:>5.1f}%")
        print(f"    200~400s 才强平（像 300s 超时）  {k300:>5}  {k300/len(gaps)*100:>5.1f}%")
        if k300 > kfast:
            print(f"\n    ⇒ **P2（持有超时）是主要出库路径**。")
            print(f"      改法：把超时从「taker 平仓」改成「**只撤加仓侧挂单、保留减仓侧挂单**」，")
            print(f"      让仓位挂着等被动成交（maker 免费），只有 P1 价格止损保留 taker 权。")
        else:
            print(f"\n    ⇒ **P1（价格止损）是主要出库路径** ⇒ 闸在工作，40bp 阈值需按成本校准。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
