# -*- coding: utf-8 -*-
"""Z9a：找到「开仓时」的初始 SL（sl_price 是活体字段，被追踪/保本改写 → 有未来函数）。"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sqlalchemy import create_engine, text  # noqa: E402

URL = os.getenv("DATABASE_URL", "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
eng = create_engine(URL)
with eng.connect() as c:
    c.execute(text("set app.is_admin='on'"))
    cols = [dict(r._mapping) for r in c.execute(text("""
        select column_name, data_type from information_schema.columns
        where table_name='paper_positions' order by ordinal_position
    """)).fetchall()]
    print("paper_positions 列：")
    print("  " + ", ".join(f"{x['column_name']}" for x in cols))

    print("\nposition_exit_events 事件类型：")
    for r in c.execute(text("""
        select event_type, count(*) n from position_exit_events group by 1 order by 2 desc
    """)).fetchall():
        print(f"  {r[0]:<28}{r[1]}")

    print("\nposition_exit_events 列：")
    ev_cols = [dict(r._mapping) for r in c.execute(text("""
        select column_name from information_schema.columns
        where table_name='position_exit_events' order by ordinal_position
    """)).fetchall()]
    print("  " + ", ".join(x["column_name"] for x in ev_cols))

    print("\nstrategy_trades 列：")
    st_cols = [dict(r._mapping) for r in c.execute(text("""
        select column_name from information_schema.columns
        where table_name='strategy_trades' order by ordinal_position
    """)).fetchall()]
    print("  " + ", ".join(x["column_name"] for x in st_cols))

    print("\n近 75 天 mid/long 已平仓样本（前 5 行关键字段）：")
    rows = [dict(r._mapping) for r in c.execute(text("""
        select id, symbol, timeframe_tier, entry_price, sl_price, tp_price, close_price,
               trailing_stop_price, peak_pnl_pct, close_reason, opened_at, closed_at,
               exit_state_json
        from paper_positions
        where timeframe_tier in ('mid','long') and status='closed'
          and closed_at >= now() - interval '75 days'
        order by opened_at limit 5
    """)).fetchall()]
    for r in rows:
        print(f"\n  #{r['id']} {r['symbol']} {r['timeframe_tier']} entry={r['entry_price']} "
              f"sl={r['sl_price']} trail={r['trailing_stop_price']} close={r['close_price']} "
              f"peak={r['peak_pnl_pct']} reason={str(r['close_reason'])[:30]}")
        es = r["exit_state_json"]
        if isinstance(es, str):
            try:
                es = json.loads(es)
            except Exception:
                pass
        if isinstance(es, dict):
            print(f"    exit_state keys={list(es.keys())[:25]}")
            for kk in ("initial_sl", "sl_price", "entry_sl", "initial_sl_price", "risk_pct",
                       "exit_policy", "sl", "stop_loss"):
                if kk in es:
                    print(f"      {kk}={str(es[kk])[:200]}")

    print("\nstrategy_trades.decision_context 是否含初始 SL（抽 3 条 mid/long）：")
    for r in c.execute(text("""
        select symbol, decision_context, signal_context from strategy_trades
        where opened_at >= now() - interval '75 days' order by opened_at desc limit 3
    """)).fetchall():
        print(f"\n  {r[0]}: decision_context={str(r[1])[:400]}")
        print(f"          signal_context={str(r[2])[:400]}")

    print("\n是否存在 SL 变更事件（metadata 里带 sl）：")
    n = c.execute(text("""
        select count(*) from position_exit_events
        where metadata_json::text ilike '%sl%'
    """)).scalar()
    print(f"  含 'sl' 的事件数={n}")
    for r in c.execute(text("""
        select event_type, metadata_json from position_exit_events
        where metadata_json::text ilike '%sl%' order by created_at desc limit 3
    """)).fetchall():
        print(f"  {r[0]}: {str(r[1])[:300]}")
