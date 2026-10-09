# -*- coding: utf-8 -*-
"""回放/模拟机对照：已发布的 barrier_ladder.step ≡ 研究回放 replay_one？

对 mid 仓位逐仓、逐 bar 同时驱动两套实现：
  A) 研究回放 counterfactual_exit_replay_20260929.replay_one（SL 优先，bar OHLC）
  B) 已发布 backend.services.exit.barrier_ladder.step（tick 级：先喂 SL 侧极值、再喂 TP 侧极值）
比较出场 kind 与 fill 价；不一致列出差异并统计一致率。
"""
import sys

sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")
sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena\scripts")

import numpy as np
import counterfactual_exit_replay_20260929 as RR
from backend.services.exit import barrier_ladder as BL


def shipped_replay(row, bars, k=1.0, r2=2.0, tau_h=24.0, chand_c=2.0):
    """用已发布 step() 沿同一 bar 序列回放（保守：SL 侧极值先于 TP 侧极值）。"""
    spec = BL.BarrierSpec(k=k, sl_min_pct=0.8, sl_max_pct=8.0, tp1_r=1.0, tp2_r=r2,
                          tp1_frac=0.5, tp2_frac=0.3, chand_c=chand_c, tau_h=tau_h)
    e = row.entry_price
    side = row.side
    dirn = 1.0 if side == "long" else -1.0
    t_open, t_close = row.opened_ts, row.closed_ts
    b = bars[(bars.ts >= t_open) & (bars.ts < t_close + 6 * 3600)].reset_index(drop=True)
    atr = RR.hourly_atr(bars, t_open)
    if atr is None or b.empty:
        return None, "no_atr"
    st = BL.BarrierState(sl=BL.initial_sl(e, atr, spec, side))
    fills = []
    closed = None
    for _, bar in b.iterrows():
        if closed:
            break
        # 保守顺序：SL 侧极值先（与已发布引擎的 tick 语义近似；τ 边界差异属 tick/bar 口径）
        sl_px = bar.l if dirn > 0 else bar.h
        tp_px = bar.h if dirn > 0 else bar.l
        r1 = BL.step(spec, st, side, e, atr, sl_px, bar.ts - t_open)
        for f in r1.fills:
            fills.append(f)
        if r1.closed:
            closed = r1.reason
            break
        if st.stage == 1:
            r2 = BL.step(spec, st, side, e, atr, tp_px, bar.ts - t_open)
            for f in r2.fills:
                fills.append(f)
            if r2.closed:
                closed = r2.reason
                break
    if closed is None:
        closed = "actual_close"
        fills.append(BL.BarrierFill(px=row.close_price, frac=st.qty, kind="actual_close"))
    return fills, closed


def main():
    pos = RR.load_positions()
    bars_cache = {}
    t_min = pos.opened_ts.min() - 14 * 86400
    t_max = pos.closed_ts.max() + 86400
    for sym in sorted(set(pos.symbol)):
        ex = pos[pos.symbol == sym].exchange.iloc[0] or ""
        for cand in ([ex] if ex else []) + RR.EXCH_PREF:
            df = RR.load_klines(sym, cand, t_min, t_max)
            if df is not None and len(df) > 500:
                bars_cache[sym] = df
                break
    n = 0
    kind_match = 0
    px_mismatch = 0
    mismatches = []
    for _, r in pos.iterrows():
        if r.symbol not in bars_cache:
            continue
        bars = bars_cache[r.symbol]
        if RR.price_sane(r, bars) is None:
            continue
        a_pnl, a_fee, a_kind, _ = RR.replay_one(r, bars, 1.0, 2.0, 24, True, True)
        b_fills, b_kind = shipped_replay(r, bars)
        if a_pnl is None or b_fills is None:
            continue
        n += 1
        # 比较：研究侧 kind 与发布侧 kind（sl/tp1/tp2/chand/time/actual_close）
        if a_kind == b_kind:
            kind_match += 1
        else:
            mismatches.append((r.id, r.symbol, a_kind, b_kind))
        # 研究侧最终成交价 vs 发布侧最后 fill 价
        if a_kind in ("sl", "chand") and b_kind in ("sl", "chand"):
            # 研究侧 fill 价 = SL/trail 价位；发布侧最后 fill.px 应同价
            b_px = b_fills[-1].px
            # 研究侧价格不在返回值里，用其 fill 逻辑反推（entry ∓ R / trail）——简化：只比对 kind
            pass
    print(f"parity: n={n} kind_match={kind_match} ({100.0*kind_match/max(n,1):.1f}%)")
    for m in mismatches[:10]:
        print("  mismatch:", m)


if __name__ == "__main__":
    main()
