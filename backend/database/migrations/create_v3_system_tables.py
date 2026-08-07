#!/usr/bin/env python3
"""
Migration: Create V3 system tables

V3升级新增表:
1. arbitrage_positions - 套利仓位记录
2. anomaly_events - 异常事件日志
3. strategy_hypotheses - 策略假设记录
4. factor_quality_reports - 因子质量报告
5. market_regime_history - 市场状态历史

Reference: docs/SYSTEM_UPGRADE_DESIGN_V3.md §7.4
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from connection import SessionLocal, engine


def upgrade():
    """Apply the migration"""
    print("Starting migration: create_v3_system_tables")

    db = SessionLocal()
    try:
        # ── 1. arbitrage_positions: 套利仓位记录 ──
        print("Creating arbitrage_positions table...")
        db.execute(text("""
            CREATE TABLE IF NOT EXISTS arbitrage_positions (
                id SERIAL PRIMARY KEY,
                position_id VARCHAR(64) UNIQUE NOT NULL,
                symbol VARCHAR(32) NOT NULL,
                strategy VARCHAR(32) NOT NULL,
                long_size DECIMAL(20, 8),
                long_entry_price DECIMAL(20, 8),
                short_size DECIMAL(20, 8),
                short_entry_price DECIMAL(20, 8),
                delta DECIMAL(20, 8),
                accumulated_funding DECIMAL(20, 8) DEFAULT 0,
                status VARCHAR(16) DEFAULT 'active',
                entry_time TIMESTAMP NOT NULL,
                close_time TIMESTAMP,
                close_reason VARCHAR(64),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """))

        db.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_arb_positions_symbol
            ON arbitrage_positions(symbol)
        """))

        db.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_arb_positions_status
            ON arbitrage_positions(status)
        """))

        db.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_arb_positions_strategy
            ON arbitrage_positions(strategy)
        """))

        # ── 2. anomaly_events: 异常事件日志 ──
        print("Creating anomaly_events table...")
        db.execute(text("""
            CREATE TABLE IF NOT EXISTS anomaly_events (
                id SERIAL PRIMARY KEY,
                event_id VARCHAR(128) UNIQUE NOT NULL,
                symbol VARCHAR(32) NOT NULL,
                anomaly_type VARCHAR(32) NOT NULL,
                severity DECIMAL(5, 4),
                z_score DECIMAL(10, 4),
                description TEXT,
                raw_value DECIMAL(20, 8),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """))

        db.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_anomaly_events_symbol
            ON anomaly_events(symbol)
        """))

        db.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_anomaly_events_type
            ON anomaly_events(anomaly_type)
        """))

        db.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_anomaly_events_created
            ON anomaly_events(created_at)
        """))

        # ── 3. strategy_hypotheses: 策略假设记录 ──
        print("Creating strategy_hypotheses table...")
        db.execute(text("""
            CREATE TABLE IF NOT EXISTS strategy_hypotheses (
                id SERIAL PRIMARY KEY,
                hypothesis_id VARCHAR(128) UNIQUE NOT NULL,
                name VARCHAR(128),
                description TEXT,
                market_regime VARCHAR(32),
                param_ranges JSONB,
                backtest_sharpe DECIMAL(10, 4),
                promoted BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """))

        db.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_strategy_hypotheses_regime
            ON strategy_hypotheses(market_regime)
        """))

        db.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_strategy_hypotheses_promoted
            ON strategy_hypotheses(promoted)
        """))

        # ── 4. factor_quality_reports: 因子质量报告 ──
        print("Creating factor_quality_reports table...")
        db.execute(text("""
            CREATE TABLE IF NOT EXISTS factor_quality_reports (
                id SERIAL PRIMARY KEY,
                factor_id VARCHAR(64) NOT NULL,
                report_date DATE NOT NULL,
                ic_mean DECIMAL(10, 6),
                icir DECIMAL(10, 6),
                coverage DECIMAL(5, 4),
                grade VARCHAR(2),
                is_alive BOOLEAN,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(factor_id, report_date)
            )
        """))

        db.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_factor_quality_factor_id
            ON factor_quality_reports(factor_id)
        """))

        db.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_factor_quality_date
            ON factor_quality_reports(report_date)
        """))

        # ── 5. market_regime_history: 市场状态历史 ──
        print("Creating market_regime_history table...")
        db.execute(text("""
            CREATE TABLE IF NOT EXISTS market_regime_history (
                id SERIAL PRIMARY KEY,
                symbol VARCHAR(32),
                regime VARCHAR(32) NOT NULL,
                confidence DECIMAL(5, 4),
                features JSONB,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """))

        db.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_regime_history_symbol
            ON market_regime_history(symbol)
        """))

        db.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_regime_history_created
            ON market_regime_history(created_at)
        """))

        db.commit()
        print("Migration completed successfully!")
        print("  - arbitrage_positions: created")
        print("  - anomaly_events: created")
        print("  - strategy_hypotheses: created")
        print("  - factor_quality_reports: created")
        print("  - market_regime_history: created")

    except Exception as e:
        db.rollback()
        print(f"Migration failed: {e}")
        raise
    finally:
        db.close()


def downgrade():
    """Rollback the migration"""
    print("Starting rollback: create_v3_system_tables")

    db = SessionLocal()
    try:
        print("Dropping V3 system tables...")
        db.execute(text("DROP TABLE IF EXISTS market_regime_history CASCADE"))
        db.execute(text("DROP TABLE IF EXISTS factor_quality_reports CASCADE"))
        db.execute(text("DROP TABLE IF EXISTS strategy_hypotheses CASCADE"))
        db.execute(text("DROP TABLE IF EXISTS anomaly_events CASCADE"))
        db.execute(text("DROP TABLE IF EXISTS arbitrage_positions CASCADE"))

        db.commit()
        print("Rollback completed successfully!")

    except Exception as e:
        db.rollback()
        print(f"Rollback failed: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description='V3 System Tables Migration')
    parser.add_argument('--rollback', action='store_true', help='Rollback the migration')
    args = parser.parse_args()

    if args.rollback:
        downgrade()
    else:
        upgrade()
