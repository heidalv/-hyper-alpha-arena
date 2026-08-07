# -*- coding: utf-8 -*-
"""临时诊断：剩余表结构"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
from sqlalchemy import text, create_engine
import os, re

def get_dsn(env):
    # 读取 .env 中的 URL
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

def columns(eng, table, dbname):
    print(f"\n===== {dbname}.{table} 列 =====")
    with eng.connect() as db:
        rows = db.execute(text(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_name=:t ORDER BY ordinal_position"), {"t": table}).fetchall()
        for r in rows:
            print("  ", r[0], r[1])

columns(ae, "llm_usage_logs", "analytics")
columns(ae, "ai_decision_logs", "analytics")
columns(ae, "trend_prediction_records", "analytics")
columns(ae, "decision_snapshots", "analytics")
columns(me, "crypto_klines", "market")
columns(me, "crypto_prices", "market")
columns(me, "kline_collection_tasks", "market")
columns(me, "kline_sync_heartbeat", "market")
columns(me, "crypto_price_ticks", "market")
columns(me, "price_samples", "market")
columns(me, "market_asset_metrics", "market")
