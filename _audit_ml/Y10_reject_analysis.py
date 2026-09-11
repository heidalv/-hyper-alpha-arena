# -*- coding: utf-8 -*-
"""减仓被拒事件分析（Y10）：保护触发 → 减半被拒 → 继续下跌 → 大亏。

position_exit_events 里 partial_exit_rejected 事件近 35 天约 600 条。
本脚本：
  1. 打印 2 个样例持仓的完整事件行（含 metadata_json）；
  2. 统计有/无「减仓被拒」事件的持仓最终 PnL 对比；
  3. 按拒绝原因（metadata）分组。
"""
from __future__ import annotations

import json
import os
import statistics as st
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
        evs = [dict(r._mapping) for r in c.execute(text("""
            select position_id, symbol, side, trade_nature, event_type, exit_channel,
                   quantity, price, pnl, close_ratio, peak_pnl_pct_at_event,
                   pnl_pct_at_event, retention_ratio, metadata_json, created_at
            from position_exit_events
            where created_at >= now() - interval '35 days'
            order by position_id, created_at
        """)).fetchall()]
        poss = {r[0]: dict(r._mapping) for r in c.execute(text("""
            select id, symbol, timeframe_tier, entry_price, close_price, leverage, size, original_size,
                   margin, original_margin, peak_pnl_pct, unrealized_pnl, partial_realized_pnl,
                   close_reason, opened_at, closed_at
            from paper_positions
            where timeframe_tier in ('mid','long') and status='closed'
              and closed_at >= now() - interval '30 days'
        """)).fetchall()}

    # 1) 样例：第一个有 rejected 事件的持仓
    rejected_pids = [e["position_id"] for e in evs if e["event_type"] == "partial_exit_rejected"]
    sample_pid = rejected_pids[0] if rejected_pids else None
    if sample_pid:
        print(f"=== 样例持仓 {sample_pid} 事件序列 ===")
        for e in [x for x in evs if x["position_id"] == sample_pid]:
            print(f"  {str(e['created_at'])[:19]} {e['event_type']:<22} "
                  f"ch={str(e['exit_channel'])[:38]:<40} px={e['price']} qty={e['quantity']} "
                  f"ratio={e['close_ratio']} pnl_pct@={e['pnl_pct_at_event']} "
                  f"peak@={e['peak_pnl_pct_at_event']} ret={e['retention_ratio']}")
            if e["metadata_json"]:
                print(f"      meta: {str(e['metadata_json'])[:300]}")

    # 2) 有/无拒绝事件的持仓对比
    rej_pids = set(rejected_pids)
    print(f"\n=== 有「减仓被拒」事件的持仓数: {len(rej_pids)} / {len(poss)} ===")
    groups = defaultdict(list)
    for pid, p in poss.items():
        entry = float(p["entry_price"] or 0)
        close = float(p["close_price"] or 0)
        if entry <= 0 or close <= 0:
            continue
        usd = (float(p["unrealized_pnl"] or 0) + float(p["partial_realized_pnl"] or 0))
        pct = (close - entry) / entry * 100
        groups["有拒绝" if pid in rej_pids else "无拒绝"].append((pct, usd, p))

    for k, v in groups.items():
        print(f"  {k:<6} n={len(v):>3} 价格均值={st.mean([x[0] for x in v]):>+7.2f}% "
              f"中位={st.median([x[0] for x in v]):>+7.2f}% "
              f"USD合计={sum(x[1] for x in v):>+9.2f} "
              f"≤-2%占比={sum(1 for x in v if x[0] <= -2)/len(v):.2f}")

    # 3) 拒绝原因分组
    print("\n=== 拒绝原因（metadata_json 关键字段）===")
    reasons = defaultdict(int)
    for e in evs:
        if e["event_type"] != "partial_exit_rejected":
            continue
        meta = e["metadata_json"]
        key = "?"
        if meta:
            try:
                d = json.loads(meta) if isinstance(meta, str) else meta
                key = str(d.get("reason") or d.get("error") or d.get("skip_reason")
                          or d.get("why") or str(d)[:60])
            except Exception:
                key = str(meta)[:60]
        reasons[key] += 1
    for k, v in sorted(reasons.items(), key=lambda x: -x[1])[:12]:
        print(f"  {k[:90]:<92} n={v}")

    # 4) 每个持仓被拒次数分布
    cnt = defaultdict(int)
    for e in evs:
        if e["event_type"] == "partial_exit_rejected":
            cnt[e["position_id"]] += 1
    if cnt:
        vals = sorted(cnt.values())
        print(f"\n=== 每仓被拒次数: 中位={st.median(vals):.0f} 均值={st.mean(vals):.1f} "
              f"max={max(vals)} (n={len(vals)}) ===")
        top = sorted(cnt.items(), key=lambda x: -x[1])[:8]
        for pid, n in top:
            p = poss.get(pid)
            if p:
                entry = float(p["entry_price"] or 0)
                close = float(p["close_price"] or 0)
                pct = (close - entry) / entry * 100 if entry > 0 else 0
                print(f"  pid={pid} {p['symbol']:<8} 被拒{n:>4}次 最终={pct:>+6.2f}% "
                      f"峰值={float(p['peak_pnl_pct'] or 0)*100:>+5.2f}% {str(p['close_reason'])[:30]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
