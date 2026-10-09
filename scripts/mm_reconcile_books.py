# -*- coding: utf-8 -*-
"""[F254 2026-09-16] 双口径对账修复：时代前残差 + 时代内残差 分别校正。

背景事故：F240 的校正行按「运行态快照时刻」落库（落在时代内），把 30 天口径对齐了，
但 `/positions`（since=stats_since 时代口径）看不到被抵消的旧时代行，只看到校正行
⇒ 幽灵持仓（XRP +$6,559 / SOL −$3,259 / ETH −$3,374，实盘空仓 ✗）。

修复语义：
  1. 时代前残差（ts < stats_since 的净 qty）→ 写**回拨到 stats_since−1s** 的校正行，
     让 30 天口径的旧时代部分归零（不进入时代视图）；
  2. 时代内残差（ts ≥ stats_since 的净 qty vs 运行态）→ 写 **state_ts** 时刻的校正行，
     让时代视图与运行态一致。
  两个口径同时对齐；校正行零盈亏（fill_px=mid_px），不制造任何盈亏。
  写之前过 [F254] 陈旧闸：运行态快照 >15 分钟未更新 ⇒ 拒绝写。
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.services.market_maker import reconcile  # noqa: E402
from backend.services import lane_ledger, lane_registry  # noqa: E402

LANE = "mm_asterdex"


def _iso(dt) -> str:
    return dt.isoformat()


def main() -> int:
    meta = reconcile.lane_meta(LANE)
    stats_since = meta.get("stats_since")
    if not stats_since:
        print("✗ 无 stats_since，中止")
        return 2
    since_dt = datetime.fromisoformat(str(stats_since))
    pre_ts = since_dt - timedelta(seconds=1)

    rt = reconcile.runtime_positions(LANE)
    age = reconcile.runtime_stale_age_sec(LANE)
    print(f"运行态: {[(k, v['qty']) for k, v in rt.items()]}")
    print(f"运行态快照年龄: {age:.0f}s（闸值 {reconcile.STALE_MAX_AGE_SEC:.0f}s）")
    if age is None or age > reconcile.STALE_MAX_AGE_SEC:
        print("✗ 运行态陈旧，拒绝写校正行（先查明保存为何停滞）")
        return 2

    # ① 时代前残差（ts < stats_since）
    pre = reconcile.scope_residues(lane_id=LANE, since=None, until=_iso(pre_ts))
    print("\n① 时代前残差（30 天口径里 stats_since 之前的净 qty）:")
    for k, v in sorted(pre.items()):
        if abs(v) > 1e-9:
            print(f"   {k}: {v:+.6f}")

    # ② 时代内残差（ts ≥ stats_since 且 ≤ state_ts）vs 运行态
    era = reconcile.scope_residues(lane_id=LANE, since=stats_since, until=None)
    print("② 时代内净 qty（账本） vs 运行态:")
    era_fix = []
    for k in sorted(set(era) | set(rt)):
        q_era = era.get(k, 0.0)
        q_rt = float((rt.get(k) or {}).get("qty") or 0.0)
        diff = q_era - q_rt
        if abs(diff) > 1e-9:
            print(f"   {k}: ledger={q_era:+.6f} runtime={q_rt:+.6f} diff={diff:+.6f}")

    n_pre = n_era = 0
    # ① 写时代前校正（回拨 stats_since−1s）
    marks = reconcile.latest_marks(list(set(pre) | set(era)), str(meta.get("venue") or "asterdex"))
    for k, v in pre.items():
        if abs(v) <= 1e-9 or (marks.get(k) or 0) <= 0:
            continue
        side = "sell" if v > 0 else "buy"
        ok = lane_ledger.record_fill(
            lane_id=LANE, symbol=k, side=side, qty=abs(v),
            fill_px=marks[k], mid_px=marks[k], fee_rate=0.0, ts=pre_ts,
            meta={"source": "reconcile", "reason": "pre_era_scope_zero",
                  "runtime_qty": 0.0, "ledger_qty": v})
        n_pre += 1 if ok else 0
    print(f"\n已写时代前校正行 {n_pre} 条（ts=stats_since-1s，零盈亏）")

    # ② 写时代内校正（state_ts）
    state_ts = None
    cmp = reconcile.compare_lane_books(lane_id=LANE)
    state_ts = cmp.get("state_ts")
    for k in sorted(set(era) | set(rt)):
        q_era = era.get(k, 0.0)
        q_rt = float((rt.get(k) or {}).get("qty") or 0.0)
        diff = q_era - q_rt
        if abs(diff) <= 1e-9 or (marks.get(k) or 0) <= 0:
            continue
        side = "sell" if diff > 0 else "buy"
        ok = lane_ledger.record_fill(
            lane_id=LANE, symbol=k, side=side, qty=abs(diff),
            fill_px=marks[k], mid_px=marks[k], fee_rate=0.0, ts=state_ts,
            meta={"source": "reconcile", "reason": "era_scope_align",
                  "runtime_qty": q_rt, "ledger_qty": q_era})
        n_era += 1 if ok else 0
    print(f"已写时代内校正行 {n_era} 条（ts=state_ts，零盈亏）")

    # 复核
    pre2 = reconcile.scope_residues(lane_id=LANE, since=None, until=_iso(pre_ts))
    era2 = reconcile.scope_residues(lane_id=LANE, since=stats_since, until=None)
    pre_ok = all(abs(v) <= 1e-9 for v in pre2.values())
    era_ok = all(abs(era2.get(k, 0.0) - float((rt.get(k) or {}).get("qty") or 0.0)) <= 1e-9
                  for k in set(era2) | set(rt))
    print(f"\n复核: 时代前归零={pre_ok}  时代内对齐={era_ok}")
    return 0 if (pre_ok and era_ok) else 1


if __name__ == "__main__":
    raise SystemExit(main())
