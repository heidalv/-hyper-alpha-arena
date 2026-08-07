#!/usr/bin/env python3
"""
Migration: Create AI Learning System Integration tables

AI学习系统深度整合新增表:
1. multi_symbol_kelly - 多币种Kelly仓位汇总
2. drl_performance - DRL表现追踪
3. drl_performance_daily - DRL表现日聚合（归档）
4. system_coordinator_state - 系统协调状态

扩展表:
5. strategy_regime_scores - 新增DRL/Kelly/版本号字段

Reference: .qoder/plans/AI学习系统深度整合方案
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from connection import SessionLocal, engine
from backend.database.dialect import dialect


def upgrade():
    """Apply the migration"""
    print("Starting migration: create_ai_learning_integration_tables")

    _pk = dialect.auto_pk()
    db = SessionLocal()
    try:
        # ── 1. multi_symbol_kelly: 多币种Kelly仓位汇总 ──
        print("Creating multi_symbol_kelly table...")
        db.execute(text("""
            CREATE TABLE IF NOT EXISTS multi_symbol_kelly (
                id """ + _pk + """,
                timestamp TIMESTAMP NOT NULL,
                symbol VARCHAR(20) NOT NULL,
                kelly_fraction REAL DEFAULT 0.0,
                adjusted_size REAL DEFAULT 0.0,
                portfolio_fraction REAL DEFAULT 0.0,
                risk_contribution REAL DEFAULT 0.0,
                correlation_with_others REAL DEFAULT 0.0,
                calculation_window INTEGER DEFAULT 252
            )
        """))

        db.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_msk_symbol_ts
            ON multi_symbol_kelly(symbol, timestamp)
        """))

        db.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_msk_timestamp
            ON multi_symbol_kelly(timestamp)
        """))

        # ── 2. drl_performance: DRL表现追踪 ──
        print("Creating drl_performance table...")
        db.execute(text("""
            CREATE TABLE IF NOT EXISTS drl_performance (
                id """ + _pk + """,
                timestamp TIMESTAMP NOT NULL,
                symbol VARCHAR(20) NOT NULL,
                predicted_direction REAL,
                actual_direction REAL,
                predicted_size REAL,
                actual_pnl REAL,
                regime VARCHAR(30),
                is_correct BOOLEAN,
                model_version VARCHAR(50),
                observation_hash VARCHAR(64)
            )
        """))

        db.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_drl_perf_symbol_ts
            ON drl_performance(symbol, timestamp)
        """))

        db.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_drl_perf_model_ver
            ON drl_performance(model_version)
        """))

        db.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_drl_perf_timestamp
            ON drl_performance(timestamp)
        """))

        # ── 3. drl_performance_daily: DRL表现日聚合 ──
        print("Creating drl_performance_daily table...")
        db.execute(text("""
            CREATE TABLE IF NOT EXISTS drl_performance_daily (
                id """ + _pk + """,
                date DATE NOT NULL,
                symbol VARCHAR(20) NOT NULL,
                model_version VARCHAR(50),
                avg_accuracy REAL DEFAULT 0.0,
                avg_pnl REAL DEFAULT 0.0,
                trade_count INTEGER DEFAULT 0,
                correct_count INTEGER DEFAULT 0,
                avg_predicted_confidence REAL DEFAULT 0.0,
                UNIQUE(date, symbol, model_version)
            )
        """))

        db.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_drl_daily_date
            ON drl_performance_daily(date)
        """))

        db.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_drl_daily_symbol
            ON drl_performance_daily(symbol)
        """))

        # ── 4. system_coordinator_state: 系统协调状态 ──
        print("Creating system_coordinator_state table...")
        db.execute(text("""
            CREATE TABLE IF NOT EXISTS system_coordinator_state (
                id """ + _pk + """,
                last_evolution_at TIMESTAMP,
                last_drl_training_at TIMESTAMP,
                current_regime VARCHAR(30),
                regime_confidence REAL DEFAULT 0.0,
                auto_tuning_enabled BOOLEAN DEFAULT 1,
                sync_status VARCHAR(20) DEFAULT 'idle',
                active_transaction_id VARCHAR(50),
                locked_systems TEXT,
                param_versions TEXT,
                last_correlation_update_at TIMESTAMP,
                last_kelly_update_at TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """))

        # ── 5. 扩展 strategy_regime_scores 表 ──
        print("Adding AI learning columns to strategy_regime_scores...")
        # SQLite: 用 ALTER TABLE ADD COLUMN（忽略已存在的列）
        for col_def in [
            ("drl_sharpe", "REAL"),
            ("kelly_avg_fraction", "REAL"),
            ("multi_symbol_correlation", "TEXT"),
            ("param_version", "INTEGER DEFAULT 0"),
        ]:
            col_name, col_type = col_def
            try:
                db.execute(text(
                    f"ALTER TABLE strategy_regime_scores ADD COLUMN {col_name} {col_type}"
                ))
                print(f"  + strategy_regime_scores.{col_name}: added")
            except Exception as e:
                if "duplicate column name" in str(e).lower():
                    print(f"  + strategy_regime_scores.{col_name}: already exists, skipped")
                else:
                    print(f"  + strategy_regime_scores.{col_name}: error: {e}")

        db.commit()
        print("Migration completed successfully!")
        print("  - multi_symbol_kelly: created")
        print("  - drl_performance: created")
        print("  - drl_performance_daily: created")
        print("  - system_coordinator_state: created")
        print("  - strategy_regime_scores: extended with DRL/Kelly columns")

    except Exception as e:
        db.rollback()
        print(f"Migration failed: {e}")
        raise
    finally:
        db.close()


def downgrade():
    """Rollback the migration"""
    print("Starting rollback: create_ai_learning_integration_tables")

    db = SessionLocal()
    try:
        print("Dropping AI learning integration tables...")
        db.execute(text("DROP TABLE IF EXISTS system_coordinator_state CASCADE"))
        db.execute(text("DROP TABLE IF EXISTS drl_performance_daily CASCADE"))
        db.execute(text("DROP TABLE IF EXISTS drl_performance CASCADE"))
        db.execute(text("DROP TABLE IF EXISTS multi_symbol_kelly CASCADE"))

        # Note: SQLite doesn't support DROP COLUMN, so extended columns remain
        print("  (strategy_regime_scores extended columns remain - SQLite limitation)")

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
    parser = argparse.ArgumentParser(description='AI Learning Integration Tables Migration')
    parser.add_argument('--rollback', action='store_true', help='Rollback the migration')
    args = parser.parse_args()

    if args.rollback:
        downgrade()
    else:
        upgrade()
