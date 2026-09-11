# -*- coding: utf-8 -*-
"""分档止盈（部分平仓）使用情况探查（X5）。

问题：mid 的 tp_stages 默认 (2.0,3.5,5.5)，而近 30 天成交峰值中位约 1.5%
→ 第一档 2% 极少触发，浮盈从未被部分锁定。本脚本查：
  1. 近 30 天 mid/long 成交中，有多少笔发生过部分平仓（partial_realized_pnl≠0）；
  2. 部分平仓的比例与档位；
  3. 「曾浮盈≥0.5% 后亏损」的 44 笔里，有部分止盈的占几笔；
  4. 当前 staged TP / tp_stages 相关 env 配置。
"""
from __future__ import annotations

import os
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine, text  # noqa: E402

ARENA_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")


def main() -> int:
    eng = create_engine(ARENA_URL)
    with eng.connect() as c:
        c.execute(text("set app.is_admin='on'"))
        rows = [dict(r._mapping) for r in c.execute(text("""
            select id, symbol, side, timeframe_tier, entry_price, close_price, peak_pnl_pct,
                   partial_realized_pnl, partial_fee_paid, original_size, size,
                   tp_level_reached, reduce_count, add_count, close_reason,
                   opened_at, closed_at
            from paper_positions
            where timeframe_tier in ('mid','long') and status='closed'
              and closed_at >= now() - interval '30 days'
            order by opened_at
        """)).fetchall()]

    n = len(rows)
    part = [r for r in rows if float(r["partial_realized_pnl"] or 0) != 0]
    tp_hit = [r for r in rows if (r["tp_level_reached"] or 0) > 0]
    reduced = [r for r in rows if (r["reduce_count"] or 0) > 0]
    print(f"近 30 天 mid/long 已平仓: {n}")
    print(f"  发生过部分平仓(partial_realized_pnl≠0): {len(part)} ({len(part)/max(1,n):.1%})")
    print(f"  tp_level_reached>0: {len(tp_hit)} ({len(tp_hit)/max(1,n):.1%})")
    print(f"  reduce_count>0: {len(reduced)} ({len(reduced)/max(1,n):.1%})")

    print("\n=== 部分平仓明细（前 15）===")
    for r in part[:15]:
        entry = float(r["entry_price"] or 0)
        close = float(r["close_price"] or 0)
        side = str(r["side"] or "long")
        raw = (close - entry) / entry * 100 if side == "long" else (entry - close) / entry * 100
        print(f"  {str(r['opened_at'])[5:16]} {r['symbol']:<8} {side:<5} 峰值={float(r['peak_pnl_pct'] or 0)*100:>+6.2f}% "
              f"最终价差={raw:>+6.2f}% 部分PnL={float(r['partial_realized_pnl'] or 0):>+7.3f} "
              f"orig={r['original_size']} left={r['size']} tp_lvl={r['tp_level_reached']} "
              f"reduce={r['reduce_count']} | {str(r['close_reason'] or '')[:24]}")

    print("\n=== 峰值分桶 × 部分平仓 ===")
    for lo, hi, lab in [(0, 0.5, "<0.5%"), (0.5, 1, "0.5-1%"), (1, 2, "1-2%"),
                        (2, 3, "2-3%"), (3, 1e9, "≥3%")]:
        v = [r for r in rows if lo <= float(r["peak_pnl_pct"] or 0) * 100 < hi]
        if not v:
            continue
        p = [r for r in v if float(r["partial_realized_pnl"] or 0) != 0]
        print(f"  峰值{lab:<8} n={len(v):>3} 部分平仓={len(p):>2} ({len(p)/len(v):.0%})")

    print("\n=== 相关 env 配置 ===")
    env_path = ROOT / ".env"
    for ln in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        s = ln.strip()
        if s.startswith("#") or "=" not in s:
            continue
        k = s.split("=", 1)[0]
        if any(t in k for t in ("STAGED", "TP_STAGE", "PARTIAL", "REDUCE", "SCALE")):
            print("  ", s[:120])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
