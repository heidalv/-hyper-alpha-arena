"""
Database Index Optimization Script
Adds composite indexes to improve query performance without deleting historical data.
"""
import sqlite3
import logging
from datetime import datetime

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

import os
DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "..", "data", "alpha_arena.db")

INDEXES_TO_CREATE = [
    # AI Decision Logs - 优化按账户和时间查询
    ("idx_ai_decision_logs_account_time", 
     "CREATE INDEX IF NOT EXISTS idx_ai_decision_logs_account_time ON ai_decision_logs(account_id, decision_time)"),
    
    # AI Decision Logs - 优化按策略和时间查询
    ("idx_ai_decision_logs_strategy_time",
     "CREATE INDEX IF NOT EXISTS idx_ai_decision_logs_strategy_time ON ai_decision_logs(ai_strategy_id, decision_time)"),
    
    # Trades - 优化按账户和时间查询
    ("idx_trades_account_time",
     "CREATE INDEX IF NOT EXISTS idx_trades_account_time ON trades(account_id, trade_time)"),
    
    # Trades - 优化按订单ID查询
    ("idx_trades_order_id",
     "CREATE INDEX IF NOT EXISTS idx_trades_order_id ON trades(order_id)"),
    
    # Orders - 优化按账户、状态和时间查询
    ("idx_orders_account_status_time",
     "CREATE INDEX IF NOT EXISTS idx_orders_account_status_time ON orders(account_id, status, created_at)"),
    
    # Orders - 优化按账户和订单号查询
    ("idx_orders_account_orderno",
     "CREATE INDEX IF NOT EXISTS idx_orders_account_orderno ON orders(account_id, order_no)"),
    
    # Positions - 优化按账户和 symbol 查询
    ("idx_positions_account_symbol",
     "CREATE INDEX IF NOT EXISTS idx_positions_account_symbol ON positions(account_id, symbol, market)"),
    
    # Strategy Trades - 优化按策略和时间查询
    ("idx_strategy_trades_strategy_time",
     "CREATE INDEX IF NOT EXISTS idx_strategy_trades_strategy_time ON strategy_trades(strategy_id, opened_at)"),
    
    # Strategy Trades - 优化按策略和状态查询
    ("idx_strategy_trades_strategy_status",
     "CREATE INDEX IF NOT EXISTS idx_strategy_trades_strategy_status ON strategy_trades(strategy_id, status)"),
    
    # Strategy Trades - 优化按 symbol 查询
    ("idx_strategy_trades_symbol_time",
     "CREATE INDEX IF NOT EXISTS idx_strategy_trades_symbol_time ON strategy_trades(symbol, opened_at)"),
    
    # AI Strategies - 优化按账户和状态查询
    ("idx_ai_strategies_account_status",
     "CREATE INDEX IF NOT EXISTS idx_ai_strategies_account_status ON ai_strategies(account_id, status)"),
    
    # Crypto Klines - 优化复合查询
    ("idx_crypto_klines_symbol_period_time",
     "CREATE INDEX IF NOT EXISTS idx_crypto_klines_symbol_period_time ON crypto_klines(symbol, period, timestamp DESC)"),
    
    # Account Asset Snapshots - 优化按账户和时间查询
    ("idx_account_snapshots_account_time",
     "CREATE INDEX IF NOT EXISTS idx_account_snapshots_account_time ON account_asset_snapshots(account_id, event_time DESC)"),
    
    # Hyperliquid Positions - 优化按账户和环境查询
    ("idx_hyperliquid_positions_account_env",
     "CREATE INDEX IF NOT EXISTS idx_hyperliquid_positions_account_env ON hyperliquid_positions(account_id, environment, snapshot_time DESC)"),
    
    # Hyperliquid Account Snapshots - 优化查询
    ("idx_hyperliquid_snapshots_account_env_time",
     "CREATE INDEX IF NOT EXISTS idx_hyperliquid_snapshots_account_env_time ON hyperliquid_account_snapshots(account_id, environment, snapshot_time DESC)"),
    
    # Signal Trigger Logs - 优化按信号池和时间查询
    ("idx_signal_trigger_logs_pool_time",
     "CREATE INDEX IF NOT EXISTS idx_signal_trigger_logs_pool_time ON signal_trigger_logs(signal_pool_id, created_at DESC)"),
    
    # Strategy Analysis Logs - 优化查询
    ("idx_strategy_analysis_logs_strategy_time",
     "CREATE INDEX IF NOT EXISTS idx_strategy_analysis_logs_strategy_time ON strategy_analysis_logs(strategy_id, created_at DESC)"),
    
    # Binance Positions - 优化查询
    ("idx_binance_positions_account_symbol_status",
     "CREATE INDEX IF NOT EXISTS idx_binance_positions_account_symbol_status ON binance_positions(account_id, symbol, status)"),
    
    # Paper Positions - 优化查询
    ("idx_paper_positions_account_symbol_status",
     "CREATE INDEX IF NOT EXISTS idx_paper_positions_account_symbol_status ON paper_positions(account_id, symbol, status)"),
    
    # Market Trades Aggregated - 优化时间序列查询
    ("idx_market_trades_aggregated_symbol_time",
     "CREATE INDEX IF NOT EXISTS idx_market_trades_aggregated_symbol_time ON market_trades_aggregated(symbol, timestamp DESC)"),
    
    # Market Orderbook Snapshots - 优化查询
    ("idx_market_orderbook_symbol_time",
     "CREATE INDEX IF NOT EXISTS idx_market_orderbook_symbol_time ON market_orderbook_snapshots(symbol, timestamp DESC)"),
    
    # Market Asset Metrics - 优化查询
    ("idx_market_asset_metrics_symbol_time",
     "CREATE INDEX IF NOT EXISTS idx_market_asset_metrics_symbol_time ON market_asset_metrics(symbol, timestamp DESC)"),
    
    # Perp Funding - 优化查询
    ("idx_perp_funding_symbol_time",
     "CREATE INDEX IF NOT EXISTS idx_perp_funding_symbol_time ON perp_funding(symbol, timestamp DESC)"),
    
    # Price Samples - 优化查询
    ("idx_price_samples_symbol_time",
     "CREATE INDEX IF NOT EXISTS idx_price_samples_symbol_time ON price_samples(symbol, sample_time DESC)"),
]


def create_indexes():
    """Create all optimized indexes"""
    logger.info(f"Starting database index optimization at {datetime.now()}")
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    created_count = 0
    skipped_count = 0
    error_count = 0
    
    for index_name, create_sql in INDEXES_TO_CREATE:
        try:
            cursor.execute(create_sql)
            if cursor.rowcount == -1:  # Index was created (or already exists)
                logger.info(f"✓ Created/verified index: {index_name}")
                created_count += 1
            else:
                logger.info(f"✓ Created index: {index_name}")
                created_count += 1
        except sqlite3.Error as e:
            if "already exists" in str(e).lower():
                logger.info(f"- Skipped (already exists): {index_name}")
                skipped_count += 1
            else:
                logger.error(f"✗ Error creating {index_name}: {e}")
                error_count += 1
    
    conn.commit()
    
    # Analyze tables to update query planner statistics
    logger.info("Analyzing tables to update query planner statistics...")
    tables_to_analyze = [
        "ai_decision_logs", "trades", "orders", "positions", "strategy_trades",
        "ai_strategies", "crypto_klines", "account_asset_snapshots",
        "hyperliquid_positions", "hyperliquid_account_snapshots",
        "signal_trigger_logs", "strategy_analysis_logs", "binance_positions",
        "paper_positions", "market_trades_aggregated", "market_orderbook_snapshots",
        "market_asset_metrics", "perp_funding", "price_samples"
    ]
    
    for table in tables_to_analyze:
        try:
            cursor.execute(f"ANALYZE {table}")
        except sqlite3.Error as e:
            logger.warning(f"Could not analyze {table}: {e}")
    
    conn.commit()
    conn.close()
    
    logger.info(f"\n{'='*50}")
    logger.info(f"Index optimization complete!")
    logger.info(f"  Created: {created_count}")
    logger.info(f"  Skipped (already exists): {skipped_count}")
    logger.info(f"  Errors: {error_count}")
    logger.info(f"{'='*50}")
    
    return created_count, skipped_count, error_count


def get_database_size():
    """Get database file size"""
    import os
    size = os.path.getsize(DB_PATH)
    return size / (1024 * 1024)  # MB


def get_table_stats():
    """Get statistics about major tables"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    tables = [
        "ai_decision_logs", "trades", "orders", "positions", 
        "strategy_trades", "ai_strategies", "crypto_klines",
        "account_asset_snapshots", "hyperliquid_positions"
    ]
    
    logger.info("\nTable Statistics:")
    logger.info("-" * 50)
    
    for table in tables:
        try:
            cursor.execute(f"SELECT COUNT(*) FROM {table}")
            count = cursor.fetchone()[0]
            logger.info(f"  {table}: {count:,} rows")
        except sqlite3.Error:
            logger.info(f"  {table}: (table not found)")
    
    conn.close()


if __name__ == "__main__":
    logger.info(f"Database path: {DB_PATH}")
    logger.info(f"Database size: {get_database_size():.2f} MB")
    
    get_table_stats()
    
    create_indexes()
    
    logger.info(f"\nFinal database size: {get_database_size():.2f} MB")
    logger.info("Index optimization completed successfully!")
