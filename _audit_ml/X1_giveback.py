# -*- coding: utf-8 -*-
"""「先盈利后大亏离场」专项解剖（第十七轮补充）。

用户观察：仍存在「先浮盈、后大亏离场」。本脚本把最近成交（含闸门上线后的新仓）
按峰值浮盈 → 最终结果分解，定位回吐路径：
  1. 峰值分桶 × 最终结果（价格口径 %，成本后）；
  2. 「峰值≥1% 且最终≤-2%」的回吐交易清单：exit 原因 / 持仓时长 / 回吐幅度；
  3. 追踪止盈是否本该激活（峰值 vs 激活阈值 3%）；
  4. 逐币/逐 close_reason 汇总。
输出：`data/long_giveback.json`
"""
from __future__ import annotations

import json
import os
import statistics as st
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine, text  # noqa: E402

ARENA_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
OUT = ROOT / "data" / "long_giveback.json"

FEE_SIDE = 0.0005
SLIP_SIDE = 0.0005
FUND_8H = 0.0001


def cost_pct(h):
    return (FEE_SIDE + SLIP_SIDE) * 2 * 100 + (h / 8.0) * FUND_8H * 100


def main() -> int:
    eng = create_engine(ARENA_URL)
    with eng.connect() as c:
        c.execute(text("set app.is_admin='on'"))
        rows = [dict(r._mapping) for r in c.execute(text("""
            select id, account_id, symbol, side, timeframe_tier, trade_nature,
                   entry_price, close_price, sl_price, tp_price, trailing_stop_price,
                   peak_pnl_pct, peak_unrealized_pnl, trough_pnl_pct,
                   unrealized_pnl, partial_realized_pnl, partial_fee_paid,
                   close_reason, opened_at, closed_at, status
            from paper_positions
            where timeframe_tier in ('mid','long')
              and status='closed'
              and closed_at >= now() - interval '14 days'
            order by closed_at desc
        """)).fetchall()]
        open_rows = [dict(r._mapping) for r in c.execute(text("""
            select id, account_id, symbol, side, timeframe_tier, entry_price, mark_price,
                   sl_price, trailing_stop_price, peak_pnl_pct, unrealized_pnl,
                   opened_at, status
            from paper_positions
            where timeframe_tier in ('mid','long') and status='open'
            order by opened_at desc
        """)).fetchall()]

    print(f"近 14 天已平仓 mid/long: {len(rows)} 笔 | 当前未平仓: {len(open_rows)} 笔")

    recs = []
    for r in rows:
        entry = float(r["entry_price"] or 0)
        close = float(r["close_price"] or 0)
        if entry <= 0 or close <= 0:
            continue
        side = str(r["side"] or "")
        hold_h = (r["closed_at"] - r["opened_at"]).total_seconds() / 3600 if r["closed_at"] else 0
        raw = (close - entry) / entry * 100 if side == "long" else (entry - close) / entry * 100
        net = raw - cost_pct(hold_h)
        peak = float(r["peak_pnl_pct"] or 0) * 100  # 该列是小数口径
        recs.append({
            "id": r["id"], "symbol": r["symbol"], "side": side, "tier": r["timeframe_tier"],
            "reason": str(r["close_reason"] or "?")[:44],
            "opened": str(r["opened_at"])[:19], "closed": str(r["closed_at"])[:19],
            "hold_h": round(hold_h, 2), "peak": round(peak, 3), "final": round(net, 3),
            "giveback": round(peak - net, 3),
            "usd": round(float(r["unrealized_pnl"] or 0) + float(r["partial_realized_pnl"] or 0)
                         - float(r["partial_fee_paid"] or 0), 3),
        })

    def agg(rows_, label):
        if not rows_:
            return
        n = len(rows_)
        print(f"  {label:<30} n={n:>3} 峰值均值={sum(x['peak'] for x in rows_)/n:>+7.2f}% "
              f"最终均值={sum(x['final'] for x in rows_)/n:>+7.2f}% "
              f"回吐均值={sum(x['giveback'] for x in rows_)/n:>+7.2f}% "
              f"USD={sum(x['usd'] for x in rows_):>+8.1f}")

    print("\n=== 峰值分桶 → 最终结果（全部方向）===")
    for lo, hi, lab in [(-1e9, 0.5, "峰值<0.5%"), (0.5, 1, "0.5-1%"), (1, 2, "1-2%"),
                        (2, 3, "2-3%"), (3, 5, "3-5%"), (5, 1e9, "≥5%")]:
        agg([x for x in recs if lo <= x["peak"] < hi], lab)

    print("\n=== 只多头 ===")
    longs = [x for x in recs if x["side"] == "long"]
    for lo, hi, lab in [(-1e9, 0.5, "峰值<0.5%"), (0.5, 1, "0.5-1%"), (1, 2, "1-2%"),
                        (2, 3, "2-3%"), (3, 5, "3-5%"), (5, 1e9, "≥5%")]:
        agg([x for x in longs if lo <= x["peak"] < hi], lab)

    print("\n=== 「先盈利后大亏」清单（峰值≥1% 且最终≤-2%）===")
    bad = sorted([x for x in recs if x["peak"] >= 1.0 and x["final"] <= -2.0],
                 key=lambda x: x["final"])
    for x in bad:
        print(f"  {x['closed'][5:16]} {x['symbol']:<8} {x['side']:<5} {x['tier']:<5} "
              f"峰值={x['peak']:>+6.2f}% 最终={x['final']:>+6.2f}% 回吐={x['giveback']:>+6.2f}% "
              f"持{x['hold_h']:>5.1f}h {x['reason'][:30]}")
    print(f"  合计 {len(bad)} 笔，USD {sum(x['usd'] for x in bad):+.1f}")

    print("\n=== 追踪止盈激活情况（峰值 vs 3% 激活阈值）===")
    for lo, hi, lab in [(0, 1.5, "峰值<1.5%（追踪不会激活）"),
                        (1.5, 3, "1.5-3%（追踪不会激活）"),
                        (3, 1e9, "≥3%（追踪会激活）")]:
        v = [x for x in recs if lo <= x["peak"] < hi]
        if v:
            n = len(v)
            print(f"  {lab:<26} n={n:>3} 最终均值={sum(x['final'] for x in v)/n:>+7.2f}% "
                  f"最终≤-2%占比={sum(1 for x in v if x['final'] <= -2)/n:.2f}")

    print("\n=== 当前未平仓（含峰值）===")
    for r in open_rows[:15]:
        entry = float(r["entry_price"] or 0)
        mark = float(r["mark_price"] or 0)
        side = str(r["side"] or "")
        if entry <= 0 or mark <= 0:
            continue
        cur = (mark - entry) / entry * 100 if side == "long" else (entry - mark) / entry * 100
        print(f"  {str(r['opened_at'])[5:16]} {r['symbol']:<8} {side:<5} 现价浮盈={cur:>+6.2f}% "
              f"峰值={float(r['peak_pnl_pct'] or 0)*100:>+6.2f}% SL={r['sl_price']} 追踪={r['trailing_stop_price']}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "n_closed": len(recs), "recs": recs,
        "giveback_list": bad,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写入 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
