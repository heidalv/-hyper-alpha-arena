# -*- coding: utf-8 -*-
"""决策→成交缺口分析（Y9）：浮盈保护触发时 vs 实际成交时的 PnL。

用 `position_exit_events`（每次出场/减仓事件的快照）对照 `paper_positions` 的最终成交：
  1. 每个持仓的事件序列（时间/事件类型/通道/价格/当时 PnL/当时峰值/保留率）；
  2. 首个「保护类」事件（峰值>0 且当时 PnL<峰值）到最终平仓之间：价格又走了多少；
  3. 按 exit_channel 汇总：触发时 PnL vs 最终 PnL 的差额（缺口）。
"""
from __future__ import annotations

import os
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine, text  # noqa: E402

ARENA_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
MARKET_URL = os.getenv("MARKET_DATABASE_URL", "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_market")


def load_klines(symbols):
    h1 = defaultdict(list)
    eng = create_engine(MARKET_URL)
    with eng.connect() as c:
        c.execute(text("set statement_timeout='900000'"))
        for exch in ("asterdex", "binance"):
            for s, ts, o, h, l, cl in c.execute(text("""
                select symbol, timestamp, open_price, high_price, low_price, close_price
                from crypto_klines where period='1h' and exchange=:ex and symbol = any(:syms)
                order by symbol, timestamp
            """), {"ex": exch, "syms": list(symbols)}).fetchall():
                h1[(exch, s)].append((int(ts), float(o), float(h), float(l), float(cl)))
    return h1


def pick(series, sym, ts):
    for ex in ("asterdex", "binance"):
        v = series.get((ex, sym))
        if v and len(v) > 200 and v[0][0] <= ts <= v[-1][0] + 86400:
            return v
    return None


def main() -> int:
    eng = create_engine(ARENA_URL)
    with eng.connect() as c:
        c.execute(text("set app.is_admin='on'"))
        poss = [dict(r._mapping) for r in c.execute(text("""
            select id, symbol, side, timeframe_tier, entry_price, close_price, leverage,
                   peak_pnl_pct, close_reason, opened_at, closed_at
            from paper_positions
            where timeframe_tier in ('mid','long') and status='closed'
              and closed_at >= now() - interval '30 days'
            order by opened_at
        """)).fetchall()]
        evs = [dict(r._mapping) for r in c.execute(text("""
            select position_id, symbol, event_type, exit_channel, price, pnl, pnl_pct_at_event,
                   peak_pnl_pct_at_event, retention_ratio, close_ratio, created_at
            from position_exit_events
            where created_at >= now() - interval '35 days'
            order by position_id, created_at
        """)).fetchall()]

    by_pos = defaultdict(list)
    for e in evs:
        by_pos[e["position_id"]].append(e)

    h1 = load_klines({p["symbol"] for p in poss})

    print(f"已平仓 mid/long {len(poss)} 笔 | 事件 {len(evs)} 条 | 有事件的持仓 "
          f"{sum(1 for p in poss if by_pos.get(p['id']))} 笔")

    print("\n=== 事件类型/通道分布 ===")
    cnt = defaultdict(int)
    for e in evs:
        cnt[(e["event_type"], e["exit_channel"])] += 1
    for k, v in sorted(cnt.items(), key=lambda x: -x[1])[:15]:
        print(f"  {str(k):<60} n={v}")

    print("\n=== 「保护类」事件 → 最终成交 的缺口（前 25 笔）===")
    print(f"{'时间':<12}{'币':<8}{'tier':<5}{'通道':<22}{'触发PnL%':>9}{'触发峰值%':>10}"
          f"{'最终%':>8}{'缺口%':>8}{'分钟':>7}")
    gaps = []
    rows_out = []
    for p in poss:
        pid = p["id"]
        evs_p = [e for e in by_pos.get(pid, []) if e["pnl_pct_at_event"] is not None]
        if not evs_p:
            continue
        entry = float(p["entry_price"] or 0)
        close = float(p["close_price"] or 0)
        if entry <= 0 or close <= 0:
            continue
        sign = 1.0 if str(p["side"]) == "long" else -1.0
        final = sign * (close - entry) / entry * 100
        # 首个「有浮盈但已回吐」的事件（保护触发点）
        prot = None
        for e in evs_p:
            pk = float(e["peak_pnl_pct_at_event"] or 0)
            cur = float(e["pnl_pct_at_event"] or 0)
            if pk > 0 and cur < pk - 1e-9:
                prot = (e, pk, cur)
                break
        if prot is None:
            continue
        e, pk, cur = prot
        mins = (p["closed_at"] - e["created_at"]).total_seconds() / 60 if p["closed_at"] else 0
        gap = final - cur
        gaps.append((gap, mins, str(e["exit_channel"] or e["event_type"] or "?")))
        rows_out.append((p, e, pk, cur, final, gap, mins))
    rows_out.sort(key=lambda x: x[5])
    for p, e, pk, cur, final, gap, mins in rows_out[:25]:
        print(f"{str(e['created_at'])[5:16]:<12}{p['symbol']:<8}{str(p['timeframe_tier']):<5}"
              f"{str(e['exit_channel'] or e['event_type'])[:21]:<22}{cur:>+9.2f}{pk:>+10.2f}"
              f"{final:>+8.2f}{gap:>+8.2f}{mins:>7.0f}")

    if gaps:
        print(f"\n  缺口统计: n={len(gaps)} 均值={st.mean([g[0] for g in gaps]):+.3f}% "
              f"中位={st.median([g[0] for g in gaps]):+.3f}% "
              f"触发→成交中位时长={st.median([g[1] for g in gaps]):.0f} 分钟")
        print(f"  缺口≤-1% 的笔数: {sum(1 for g in gaps if g[0] <= -1)}/{len(gaps)}")
        print(f"  触发时已为负(保护已晚)的笔数: {sum(1 for g in gaps if g[2] < 0)}/{len(gaps)}")

    print("\n=== 按通道汇总缺口 ===")
    by_ch = defaultdict(list)
    for gap, mins, ch in gaps:
        by_ch[ch].append((gap, mins))
    for ch in sorted(by_ch, key=lambda k: -len(by_ch[k])):
        v = by_ch[ch]
        print(f"  {ch[:34]:<36} n={len(v):>3} 缺口均值={st.mean([x[0] for x in v]):>+7.3f}% "
              f"中位={st.median([x[0] for x in v]):>+7.3f}% "
              f"时长中位={st.median([x[1] for x in v]):>6.0f}min")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
