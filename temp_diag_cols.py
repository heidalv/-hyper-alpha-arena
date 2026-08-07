# -*- coding: utf-8 -*-
"""临时诊断：检查表结构 + RLS 穿透"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
from sqlalchemy import text
from backend.database.connection import engine, market_engine, AnalyticsSessionLocal

def columns(eng, table, dbname):
    print(f"\n===== {dbname}.{table} 列 =====")
    with eng.connect() as db:
        db.execute(text("SET app.is_admin='on'"))
        rows = db.execute(text(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_name=:t ORDER BY ordinal_position"), {"t": table}).fetchall()
        for r in rows:
            print("  ", r[0], r[1])

columns(engine, "full_auto_sessions", "core")
columns(engine, "paper_positions", "core")
columns(engine, "coordinator_actions", "core")
columns(engine, "auto_coin_selections", "core")
columns(engine, "decision_snapshots", "core")
columns(AnalyticsSessionLocal, "llm_usage_logs", "analytics")
columns(AnalyticsSessionLocal, "ai_decision_logs", "analytics")
columns(AnalyticsSessionLocal, "trend_prediction_records", "analytics")
columns(market_engine, "crypto_klines", "market")
columns(market_engine, "crypto_prices", "market")
columns(market_engine, "kline_collection_tasks", "market")
columns(market_engine, "kline_sync_heartbeat", "market")
