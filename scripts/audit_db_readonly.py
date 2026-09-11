# -*- coding: utf-8 -*-
"""一次性只读审计 v2：autocommit 防事务污染。"""
import sys
sys.path.insert(0, "D:/001Alpha/Hyper-Alpha-Arena")
from sqlalchemy import create_engine, text

MARKET_URL = "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_market"
CORE_URL = "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena"
eng = create_engine(MARKET_URL, pool_pre_ping=True, isolation_level="AUTOCOMMIT")
core = create_engine(CORE_URL, pool_pre_ping=True, isolation_level="AUTOCOMMIT")

NOW_MS = 1788243000000  # 近似当前时间（与最新写入对比用）

def q(db, sql, label, params=None):
    try:
        rows = db.execute(text(sql), params or {}).fetchall()
        print(f"\n=== {label} ===")
        for r in rows[:30]:
            print("  ", r)
        if not rows:
            print("   (0 rows)")
    except Exception as e:
        print(f"\n=== {label} ===\n   ERROR: {type(e).__name__}: {str(e)[:220]}")

with eng.connect() as db:
    # 资金费率新鲜度（timestamp 是 bigint ms）
    q(db, """
        SELECT exchange, MAX(timestamp) last_ms,
               ROUND((EXTRACT(EPOCH FROM NOW())*1000 - MAX(timestamp))/3600000.0, 2) AS hours_ago
        FROM perp_funding GROUP BY exchange ORDER BY hours_ago
    """, "perp_funding 各所新鲜度(h)")

    q(db, """
        SELECT exchange, symbol, COUNT(*) n FROM perp_funding
        WHERE exchange != 'hyperliquid' GROUP BY exchange, symbol
        ORDER BY exchange, n DESC LIMIT 20
    """, "多所资金费 各所×币种行数")

    # 鲸鱼大单
    q(db, """
        SELECT activity_type, COUNT(*) n, COUNT(DISTINCT symbol) syms,
               MIN(timestamp) first_ts, MAX(timestamp) last_ts
        FROM whale_activities GROUP BY activity_type ORDER BY n DESC
    """, "whale_activities by activity_type")

    q(db, """
        SELECT activity_type, symbol, direction, amount_usd, signal_direction,
               from_entity, to_entity, blockchain, timestamp
        FROM whale_activities ORDER BY timestamp DESC LIMIT 15
    """, "whale_activities 最新 15 行")

    q(db, """
        SELECT MAX(timestamp) last_ts,
               ROUND((EXTRACT(EPOCH FROM NOW())*1000 - MAX(timestamp))/60000.0, 1) AS min_ago
        FROM whale_activities WHERE activity_type='aggregate_whale'
    """, "aggregate_whale 新鲜度(min)")

    # market_trades_aggregated（CVD/成交聚合）
    q(db, """
        SELECT symbol, exchange, COUNT(*) n, MIN(timestamp), MAX(timestamp)
        FROM market_trades_aggregated GROUP BY symbol, exchange ORDER BY n DESC LIMIT 15
    """, "market_trades_aggregated by symbol/exchange")

    q(db, """
        SELECT symbol, exchange, taker_buy_volume, taker_sell_volume, cvd,
               open_interest, funding_rate, timestamp
        FROM market_trades_aggregated ORDER BY timestamp DESC LIMIT 6
    """, "market_trades_aggregated 最新行（列探测）")

    # flow_archive_5m
    q(db, """
        SELECT * FROM flow_archive_5m ORDER BY 1 DESC LIMIT 3
    """, "flow_archive_5m 最新（列探测）")

    # symbol_aux_timeseries（链上辅助数据）
    q(db, """
        SELECT column_name FROM information_schema.columns
        WHERE table_name='symbol_aux_timeseries' ORDER BY ordinal_position
    """, "symbol_aux_timeseries 列")

    q(db, """
        SELECT * FROM symbol_aux_timeseries ORDER BY 1 DESC LIMIT 4
    """, "symbol_aux_timeseries 最新（列探测）")

    # market_asset_metrics funding 填充率
    q(db, """
        SELECT COUNT(*) total, COUNT(funding_rate) fr_filled, COUNT(open_interest) oi_filled
        FROM market_asset_metrics
    """, "market_asset_metrics funding/OI 填充率")

    q(db, """
        SELECT symbol, funding_rate, open_interest, mark_price, timestamp
        FROM market_asset_metrics WHERE funding_rate IS NOT NULL
        ORDER BY timestamp DESC LIMIT 6
    """, "market_asset_metrics 带 funding 最新行")

    # liquidation_events
    q(db, """
        SELECT symbol, exchange, COUNT(*) n, MAX(timestamp)
        FROM liquidation_events GROUP BY symbol, exchange ORDER BY n DESC LIMIT 10
    """, "liquidation_events by symbol/exchange")

with core.connect() as db:
    q(db, """
        SELECT COUNT(*) n, MIN(settled_at), MAX(settled_at), SUM(funding_amount) total
        FROM paper_funding_ledger
    """, "paper_funding_ledger 行数/时段/总额")

print("\nDONE")
