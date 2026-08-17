"""
Add BinancePositions table to track Binance futures/spot positions

This migration creates a table to store Binance trading positions separately
from Hyperliquid positions, as they have different structures and requirements.

Run: python -m backend.database.migrations.add_binance_positions_table
"""
import sys
import os
from pathlib import Path

# Add backend to path
backend_path = Path(__file__).parent.parent.parent
sys.path.insert(0, str(backend_path))

from sqlalchemy import text
from backend.database.connection import engine, SessionLocal
from backend.database.models import Base

# 定义币安持仓表结。
BINANCE_POSITIONS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS binance_positions (
    id SERIAL PRIMARY KEY,
    account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,

    -- Position identifiers
    position_id VARCHAR(100) NOT NULL UNIQUE,  -- Binance position ID (same as order_id for new positions)
    order_id VARCHAR(100),  -- Opening order ID

    -- Symbol information
    symbol VARCHAR(50) NOT NULL,  -- Trading pair (e.g., "ETH/USDT", "ETHUSDT")

    -- Position details
    side VARCHAR(10) NOT NULL,  -- 'long' or 'short'
    size DECIMAL(18, 8) NOT NULL,  -- Position size (positive for both long and short)

    -- Price information
    entry_price DECIMAL(18, 8),  -- Average entry price
    mark_price DECIMAL(18, 8),  -- Current mark price from exchange
    liquidation_price DECIMAL(18, 8),  -- Liquidation price (for futures)

    -- Profit and loss
    unrealized_pnl DECIMAL(18, 8),  -- Unrealized PnL
    realized_pnl DECIMAL(18, 8),  -- Realized PnL (after position closed)

    -- Leverage and margin (futures only)
    leverage INTEGER,  -- Leverage multiplier (1-125 for futures)
    margin_type VARCHAR(20),  -- 'cross' or 'isolated'
    notional_value DECIMAL(18, 8),  -- Position value (size * mark_price)

    -- Take Profit and Stop Loss
    tp_order_id VARCHAR(100),  -- Take profit order ID
    tp_price DECIMAL(18, 8),  -- Take profit price
    sl_order_id VARCHAR(100),  -- Stop loss order ID
    sl_price DECIMAL(18, 8),  -- Stop loss price

    -- Position status
    status VARCHAR(20) NOT NULL DEFAULT 'open',  -- 'open', 'closed', 'closing'
    position_side VARCHAR(20),  -- 'LONG' or 'SHORT' (for futures dual-side mode)

    -- Timestamps
    opened_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    closed_at TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,  -- Last sync with Binance API

    -- Indexes
    CONSTRAINT unique_open_position UNIQUE (account_id, symbol, status)
        WHERE status = 'open'
);

-- Create indexes for better query performance
CREATE INDEX IF NOT EXISTS idx_binance_positions_account_id ON binance_positions(account_id);
CREATE INDEX IF NOT EXISTS idx_binance_positions_symbol ON binance_positions(symbol);
CREATE INDEX IF NOT EXISTS idx_binance_positions_status ON binance_positions(status);
CREATE INDEX IF NOT EXISTS idx_binance_positions_opened_at ON binance_positions(opened_at DESC);

-- Create index for active positions lookup
CREATE INDEX IF NOT EXISTS idx_binance_positions_active ON binance_positions(account_id, status)
    WHERE status = 'open';

-- Comment for documentation
COMMENT ON TABLE binance_positions IS 'Stores Binance trading positions (futures and spot)';
COMMENT ON COLUMN binance_positions.position_id IS 'Unique identifier from Binance (order ID for opened positions)';
COMMENT ON COLUMN binance_positions.side IS 'long or short direction of the position';
COMMENT ON COLUMN binance_positions.status IS 'open, closed, or closing';
COMMENT ON COLUMN binance_positions.synced_at IS 'Last time this position was synced with Binance API';
"""


def migrate():
    """Create the binance_positions table"""
    db = SessionLocal()
    try:
        # Check if table already exists
        result = db.execute(text("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables
                WHERE table_schema = 'public'
                AND table_name = 'binance_positions'
            );
        """))
        table_exists = result.scalar()

        if table_exists:
            print("??binance_positions table already exists, skipping migration")
            return

        # Create the table
        print("Creating binance_positions table...")
        db.execute(text(BINANCE_POSITIONS_TABLE_SQL))
        db.commit()
        print("??Successfully created binance_positions table")

        # Verify table creation
        result = db.execute(text("""
            SELECT COUNT(*) FROM information_schema.tables
            WHERE table_schema = 'public'
            AND table_name = 'binance_positions';
        """))
        count = result.scalar()
        if count > 0:
            print("??Verification successful: binance_positions table exists")
        else:
            print("??Verification failed: table was not created")

    except Exception as e:
        db.rollback()
        print(f"??Migration failed: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    print("=" * 60)
    print("Binance Positions Table Migration")
    print("=" * 60)
    migrate()
    print("=" * 60)
