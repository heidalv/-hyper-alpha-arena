# -*- coding: utf-8 -*-
"""临时诊断：长线策略链路数据体检（admin 穿透 RLS）"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
from sqlalchemy import text
from backend.database.connection import engine, market_engine, AnalyticsSessionLocal

def q(label, sql, db, params=None):
    print(f"\n===== {label} =====")
    try:
        rows = db.execute(text(sql), params or {}).fetchall()
        if not rows:
            print("  (空)")
        for r in rows:
            print("  ", dict(r._mapping) if hasattr(r, '_mapping') else r)
    except Exception as e:
        print(f"  ERROR: {e}")

with engine.connect() as db:
    q("full_auto_sessions", """
        SELECT session_id, status, trading_mode, account_id, paper_account_id, symbols, auto_coin_symbols,
               auto_coin_enabled, created_at, started_at, updated_at, total_trades, total_pnl
        FROM full_auto_sessions ORDER BY created_at DESC LIMIT 5
    """, db)

    q("paper_positions open", """
        SELECT id, account_id, symbol, side, trade_nature, timeframe_tier, open_price, size,
               status, opened_at, unrealized_pnl
        FROM paper_positions WHERE status='open' ORDER BY opened_at DESC LIMIT 20
    """, db)

    q("paper_positions closed 近20", """
        SELECT id, account_id, symbol, side, trade_nature, timeframe_tier, status,
               opened_at, closed_at, realized_pnl
        FROM paper_positions WHERE status='closed' ORDER BY closed_at DESC NULLS LAST LIMIT 20
    """, db)

    q("paper_orders 近20", """
        SELECT id, account_id, symbol, side, order_type, price, filled_qty, status, created_at
        FROM paper_orders ORDER BY created_at DESC LIMIT 20
    """, db)

    q("coordinator_actions 近20", """
        SELECT id, session_id, symbol, action, reason, created_at
        FROM coordinator_actions ORDER BY id DESC LIMIT 20
    """, db)

    q("auto_coin_selections 近10", """
        SELECT id, session_id, symbol, status, added_at
        FROM auto_coin_selections ORDER BY id DESC LIMIT 10
    """, db)

with AnalyticsSessionLocal() as adb:
    q("mlto_thesis (全部)", """
        SELECT thesis_id, session_id, symbol, tier, direction, llm_conviction, hub_composite,
               open_readiness, tranche_stage, updated_at, created_at
        FROM mlto_thesis ORDER BY updated_at DESC LIMIT 30
    """, adb)

    q("mlto_thesis_events (近30条)", """
        SELECT id, thesis_id, event_type, payload_json, ts
        FROM mlto_thesis_events ORDER BY ts DESC LIMIT 30
    """, adb)

    q("llm_usage_logs (近30条)", """
        SELECT id, caller, model, status, content_chars, reasoning_chars, latency_ms, created_at
        FROM llm_usage_logs ORDER BY id DESC LIMIT 30
    """, adb)

    q("llm_usage_logs 各caller计数(近24h)", """
        SELECT caller, status, COUNT(*) AS cnt, MAX(created_at) AS last_ts
        FROM llm_usage_logs WHERE created_at >= NOW() - INTERVAL '24 hours'
        GROUP BY caller, status ORDER BY cnt DESC LIMIT 30
    """, adb)

    q("ai_decision_logs 近20", """
        SELECT id, session_id, symbol, tier, decision, confidence, created_at
        FROM ai_decision_logs ORDER BY id DESC LIMIT 20
    """, adb)

    q("trend_prediction_records 近20", """
        SELECT id, symbol, prediction, confidence, created_at
        FROM trend_prediction_records ORDER BY id DESC LIMIT 20
    """, adb)

with market_engine.connect() as mdb:
    q("crypto_klines 最新时间（各币1h/4h/1d）", """
        SELECT symbol, interval, MAX(ts) AS last_ts, COUNT(*) AS cnt
        FROM crypto_klines WHERE symbol IN ('BTC','ETH','SOL') AND interval IN ('1h','4h','1d')
        GROUP BY symbol, interval ORDER BY symbol, interval
    """, mdb)

    q("crypto_prices 最新", """
        SELECT symbol, ts, price, source FROM crypto_prices
        ORDER BY ts DESC LIMIT 15
    """, mdb)

    q("kline_collection_tasks 近10", """
        SELECT id, symbol, interval, status, last_run_at, error_message
        FROM kline_collection_tasks ORDER BY id DESC LIMIT 10
    """, mdb)

    q("kline_sync_heartbeat", """
        SELECT * FROM kline_sync_heartbeat ORDER BY 1 DESC LIMIT 5
    """, mdb)
