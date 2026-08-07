# -*- coding: utf-8 -*-
"""临时诊断 v2：长线策略链路数据体检（admin 穿透 RLS）"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
from sqlalchemy import text, create_engine
from datetime import datetime, timedelta

def get_dsn(env):
    with open(r"D:\001Alpha\Hyper-Alpha-Arena\.env", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if line.startswith(env + "="):
                return line.split("=", 1)[1].strip()
    return None

ANALYTICS_URL = get_dsn("ANALYTICS_DATABASE_URL")
MARKET_URL = get_dsn("MARKET_DATABASE_URL")
ae = create_engine(ANALYTICS_URL)
me = create_engine(MARKET_URL)

def q(label, eng, sql, params=None):
    print(f"\n===== {label} =====")
    try:
        with eng.connect() as db:
            db.execute(text("SET app.is_admin='on'"))
            rows = db.execute(text(sql), params or {}).fetchall()
            if not rows:
                print("  (空)")
            for r in rows:
                print("  ", dict(r._mapping))
    except Exception as e:
        print(f"  ERROR: {e}")

# ── core 库 ──
from backend.database.connection import engine as core_engine
q("full_auto_sessions (全部)", core_engine, """
    SELECT session_id, status, trading_mode, account_id, paper_account_id, tenant_id,
           symbols, auto_coin_symbols, auto_coin_enabled, auto_coin_max_slots,
           created_at, started_at, updated_at, total_trades, total_pnl
    FROM full_auto_sessions ORDER BY created_at DESC LIMIT 10
""")

q("paper_positions open", core_engine, """
    SELECT id, account_id, symbol, side, trade_nature, timeframe_tier, entry_price, size,
           status, opened_at, unrealized_pnl, tenant_id
    FROM paper_positions WHERE status='open' ORDER BY opened_at DESC LIMIT 30
""")

q("paper_positions closed 近10 (swing/trend)", core_engine, """
    SELECT id, account_id, symbol, side, trade_nature, timeframe_tier, status,
           opened_at, closed_at, realized_pnl
    FROM paper_positions WHERE status='closed'
      AND (trade_nature IN ('swing','trend_follow','position') OR timeframe_tier IN ('mid','long'))
    ORDER BY closed_at DESC NULLS LAST LIMIT 10
""")

q("coordinator_actions 近10", core_engine, """
    SELECT id, ts, action, skipped_reasons FROM coordinator_actions ORDER BY id DESC LIMIT 10
""")

q("auto_coin_selections 近10", core_engine, """
    SELECT id, session_id, symbol, action, suggested_tier, ai_confidence, created_at
    FROM auto_coin_selections ORDER BY id DESC LIMIT 10
""")

# ── analytics 库 ──
q("llm_usage_logs 近30条", ae, """
    SELECT id, account_id, provider, model, call_type, success, total_tokens,
           reasoning_tokens, duration_ms, created_at
    FROM llm_usage_logs ORDER BY id DESC LIMIT 30
""")

q("llm_usage_logs 近24h各call_type计数", ae, """
    SELECT call_type, success, COUNT(*) AS cnt, MAX(created_at) AS last_ts
    FROM llm_usage_logs WHERE created_at >= NOW() - INTERVAL '24 hours'
    GROUP BY call_type, success ORDER BY cnt DESC LIMIT 30
""")

q("decision_snapshots analytics 近20", ae, """
    SELECT id, session_id, strategy_id, symbol, tier, action, direction, confidence,
           source_lane, executed, timestamp
    FROM decision_snapshots ORDER BY id DESC LIMIT 20
""")

q("decision_snapshots analytics 近7天 tier分布", ae, """
    SELECT tier, source_lane, COUNT(*) AS cnt, MAX(timestamp) AS last_ts
    FROM decision_snapshots WHERE timestamp >= NOW() - INTERVAL '7 days'
    GROUP BY tier, source_lane ORDER BY cnt DESC LIMIT 20
""")

q("mlto_thesis 各session/symbol/tier", ae, """
    SELECT thesis_id, session_id, symbol, tier, direction, llm_conviction, hub_composite,
           open_readiness, tranche_stage, updated_at, created_at
    FROM mlto_thesis ORDER BY updated_at DESC LIMIT 30
""")

q("mlto_thesis_events 近12h", ae, """
    SELECT id, thesis_id, event_type, ts FROM mlto_thesis_events
    WHERE ts >= NOW() - INTERVAL '12 hours' ORDER BY ts DESC LIMIT 30
""")

q("mlto_memory_events 近12h", ae, """
    SELECT id, thesis_id, layer, source, signal, ts FROM mlto_memory_events
    WHERE ts >= NOW() - INTERVAL '12 hours' ORDER BY ts DESC LIMIT 30
""")

# ── market 库 ──
q("crypto_klines 最新（BTC 各周期）", me, """
    SELECT symbol, period, MAX(datetime_str) AS last_dt, COUNT(*) AS cnt
    FROM crypto_klines WHERE symbol='BTC' AND period IN ('1h','4h','1d')
    GROUP BY symbol, period ORDER BY period
""")

q("crypto_price_ticks 最新", me, """
    SELECT symbol, market, price, event_time FROM crypto_price_ticks
    ORDER BY event_time DESC LIMIT 15
""")

q("kline_sync_heartbeat", me, """
    SELECT * FROM kline_sync_heartbeat ORDER BY id DESC LIMIT 10
""")

q("market_asset_metrics 最新 (BTC)", me, """
    SELECT symbol, timestamp, funding_rate, mark_price, open_interest
    FROM market_asset_metrics WHERE symbol='BTC'
    ORDER BY timestamp DESC LIMIT 5
""")
