"""
Migration: Create Factor Performance and Review Report Tables

This migration adds tables for:
1. factor_performance_logs - Factor performance tracking
2. review_reports - Review report storage
3. strategy_performance_snapshots - Strategy performance snapshots
4. Enhanced ai_decision_logs with factor tracking fields
5. Enhanced risk_control_configs with dynamic SL/TP fields

Author: Hyper-Alpha-Arena
"""

import logging
from sqlalchemy import text
from backend.database.connection import get_engine

logger = logging.getLogger(__name__)


def upgrade():
    """Execute the migration with idempotency checks."""
    engine = get_engine()
    
    with engine.connect() as conn:
        # 1. Create factor_performance_logs table
        _create_factor_performance_logs(conn)
        
        # 2. Create review_reports table
        _create_review_reports(conn)
        
        # 3. Create strategy_performance_snapshots table
        _create_strategy_performance_snapshots(conn)
        
        # 4. Enhance ai_decision_logs table
        _enhance_ai_decision_logs(conn)
        
        # 5. Enhance risk_control_configs table
        _enhance_risk_control_configs(conn)
        
        conn.commit()
    
    logger.info("Migration completed: factor_performance_and_review_reports")


def _create_factor_performance_logs(conn):
    """Create factor_performance_logs table if not exists."""
    # Check if table exists
    result = conn.execute(text("""
        SELECT EXISTS (
            SELECT FROM information_schema.tables 
            WHERE table_name = 'factor_performance_logs'
        )
    """))
    exists = result.scalar()
    
    if exists:
        logger.debug("Table factor_performance_logs already exists")
        return
    
    conn.execute(text("""
        CREATE TABLE factor_performance_logs (
            id SERIAL PRIMARY KEY,
            factor_name VARCHAR(50) NOT NULL,
            factor_category VARCHAR(30) NOT NULL,
            ic_value DECIMAL(10, 6),
            decay_rate DECIMAL(10, 6),
            current_weight DECIMAL(10, 6),
            market_regime VARCHAR(30),
            symbol VARCHAR(20),
            timeframe VARCHAR(10),
            recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """))
    
    # Create indexes
    conn.execute(text("""
        CREATE INDEX IF NOT EXISTS idx_factor_name_time 
        ON factor_performance_logs (factor_name, recorded_at)
    """))
    
    conn.execute(text("""
        CREATE INDEX IF NOT EXISTS idx_factor_category 
        ON factor_performance_logs (factor_category)
    """))
    
    conn.execute(text("""
        CREATE INDEX IF NOT EXISTS idx_factor_symbol 
        ON factor_performance_logs (symbol)
    """))
    
    logger.info("Created table: factor_performance_logs")


def _create_review_reports(conn):
    """Create review_reports table if not exists."""
    # Check if table exists
    result = conn.execute(text("""
        SELECT EXISTS (
            SELECT FROM information_schema.tables 
            WHERE table_name = 'review_reports'
        )
    """))
    exists = result.scalar()
    
    if exists:
        logger.debug("Table review_reports already exists")
        return
    
    conn.execute(text("""
        CREATE TABLE review_reports (
            id SERIAL PRIMARY KEY,
            account_id INTEGER,
            wallet_address VARCHAR(100),
            report_type VARCHAR(20) NOT NULL,
            report_date DATE NOT NULL,
            report_content TEXT,
            metrics JSONB,
            recommendations JSONB,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(wallet_address, report_type, report_date)
        )
    """))
    
    # Create indexes
    conn.execute(text("""
        CREATE INDEX IF NOT EXISTS idx_review_wallet 
        ON review_reports (wallet_address)
    """))
    
    conn.execute(text("""
        CREATE INDEX IF NOT EXISTS idx_review_type_date 
        ON review_reports (report_type, report_date)
    """))
    
    logger.info("Created table: review_reports")


def _create_strategy_performance_snapshots(conn):
    """Create strategy_performance_snapshots table if not exists."""
    # Check if table exists
    result = conn.execute(text("""
        SELECT EXISTS (
            SELECT FROM information_schema.tables 
            WHERE table_name = 'strategy_performance_snapshots'
        )
    """))
    exists = result.scalar()
    
    if exists:
        logger.debug("Table strategy_performance_snapshots already exists")
        return
    
    conn.execute(text("""
        CREATE TABLE strategy_performance_snapshots (
            id SERIAL PRIMARY KEY,
            account_id INTEGER,
            wallet_address VARCHAR(100),
            snapshot_time TIMESTAMP NOT NULL,
            total_trades INTEGER,
            win_rate DECIMAL(10, 4),
            profit_loss_ratio DECIMAL(10, 4),
            sharpe_ratio DECIMAL(10, 4),
            max_drawdown DECIMAL(10, 4),
            factor_weights JSONB,
            market_regime_distribution JSONB,
            symbol_performance JSONB,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """))
    
    # Create indexes
    conn.execute(text("""
        CREATE INDEX IF NOT EXISTS idx_snapshot_wallet 
        ON strategy_performance_snapshots (wallet_address)
    """))
    
    conn.execute(text("""
        CREATE INDEX IF NOT EXISTS idx_snapshot_time 
        ON strategy_performance_snapshots (snapshot_time)
    """))
    
    logger.info("Created table: strategy_performance_snapshots")


def _enhance_ai_decision_logs(conn):
    """Add new columns to ai_decision_logs table."""
    columns_to_add = [
        ("signal_strength", "DECIMAL(10, 4)"),
        ("factor_scores", "JSONB"),
        ("position_sizing_details", "JSONB"),
        ("market_regime_at_decision", "VARCHAR(30)"),
    ]
    
    for column_name, column_type in columns_to_add:
        _add_column_if_not_exists(conn, "ai_decision_logs", column_name, column_type)


def _enhance_risk_control_configs(conn):
    """Add new columns to risk_control_configs table."""
    # First check if table exists
    result = conn.execute(text("""
        SELECT EXISTS (
            SELECT FROM information_schema.tables 
            WHERE table_name = 'risk_control_configs'
        )
    """))
    exists = result.scalar()
    
    if not exists:
        logger.debug("Table risk_control_configs does not exist, skipping enhancement")
        return
    
    columns_to_add = [
        ("dynamic_sl_enabled", "VARCHAR(10) DEFAULT 'false'"),
        ("sl_atr_multiple", "DECIMAL(10, 4) DEFAULT 2.5"),
        ("trailing_stop_type", "VARCHAR(30) DEFAULT 'parabolic_sar'"),
        ("tp_levels", "JSONB"),
        ("volatility_adjustment_enabled", "VARCHAR(10) DEFAULT 'true'"),
    ]
    
    for column_name, column_type in columns_to_add:
        _add_column_if_not_exists(conn, "risk_control_configs", column_name, column_type)


def _add_column_if_not_exists(conn, table_name: str, column_name: str, column_type: str):
    """Add a column to a table if it doesn't exist."""
    result = conn.execute(text(f"""
        SELECT EXISTS (
            SELECT FROM information_schema.columns 
            WHERE table_name = :table_name 
            AND column_name = :column_name
        )
    """), {"table_name": table_name, "column_name": column_name})
    exists = result.scalar()
    
    if exists:
        logger.debug(f"Column {column_name} already exists in {table_name}")
        return
    
    try:
        conn.execute(text(f"""
            ALTER TABLE {table_name} 
            ADD COLUMN IF NOT EXISTS {column_name} {column_type}
        """))
        logger.info(f"Added column {column_name} to {table_name}")
    except Exception as e:
        logger.warning(f"Could not add column {column_name} to {table_name}: {e}")


def downgrade():
    """Rollback the migration (optional, for completeness)."""
    engine = get_engine()
    
    with engine.connect() as conn:
        # Note: We typically don't drop tables in production
        # This is here for development/testing purposes
        logger.warning("Downgrade not implemented for safety")
        pass


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    upgrade()
