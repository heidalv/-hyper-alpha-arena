"""Add AI strategy and training related tables and fields.

This migration is idempotent and safe to run multiple times.
It will:
- Extend ai_decision_logs with AI strategy tracking fields
- Create ai_strategies table
- Create strategy_memories table
- Create strategy_trades table
- Create prompt_training_records table
- Create signal_performance_history table
"""
import os
import sys

from sqlalchemy import inspect, text

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT_ROOT = os.path.dirname(BASE_DIR)
sys.path.insert(0, PROJECT_ROOT)

from backend.database.connection import engine  # noqa: E402


def table_exists(inspector, table: str) -> bool:
    return table in inspector.get_table_names()


def column_exists(inspector, table: str, column: str) -> bool:
    return column in {col["name"] for col in inspector.get_columns(table)}


def index_exists(inspector, table: str, index_name: str) -> bool:
    return index_name in {idx["name"] for idx in inspector.get_indexes(table)}


def upgrade() -> None:
    inspector = inspect(engine)

    with engine.begin() as conn:
        # 1) Extend ai_decision_logs with AI strategy tracking fields
        table = "ai_decision_logs"
        strategy_fields = [
            ("ai_strategy_id", "VARCHAR(50)", True),
            ("strategy_version", "INTEGER", False),
            ("decision_quality_score", "DECIMAL(10,4)", False),
        ]

        if table in inspector.get_table_names():
            for col_name, col_type, needs_index in strategy_fields:
                if not column_exists(inspector, table, col_name):
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_type}"))
                    print(f"✅ Added {col_name} to {table}")

                    if needs_index:
                        index_name = f"ix_{table}_{col_name}"
                        if not index_exists(inspector, table, index_name):
                            conn.execute(text(f"CREATE INDEX {index_name} ON {table} ({col_name})"))
                            print(f"✅ Created index {index_name}")
                else:
                    print(f"ℹ️  {col_name} already exists on {table}")

        # 2) Create ai_strategies table
        if not table_exists(inspector, "ai_strategies"):
            conn.execute(
                text(
                    """
                    CREATE TABLE ai_strategies (
                        id SERIAL PRIMARY KEY,
                        strategy_id VARCHAR(50) UNIQUE NOT NULL,
                        name VARCHAR(200) NOT NULL,
                        description TEXT,

                        -- 提示词配置
                        master_prompt_template_id INTEGER REFERENCES prompt_templates(id),
                        prompt_version INTEGER DEFAULT 1,
                        prompt_variables JSONB DEFAULT '{}',

                        -- 触发配置
                        signal_pool_ids JSONB DEFAULT '[]',
                        trigger_mode VARCHAR(20) DEFAULT 'hybrid',
                        trigger_interval INTEGER,

                        -- 因子配置
                        enabled_factors JSONB DEFAULT '[]',
                        factor_weights JSONB DEFAULT '{}',

                        -- 风险配置
                        max_position_size FLOAT DEFAULT 0.2,
                        stop_loss_pct FLOAT DEFAULT 0.05,
                        take_profit_pct FLOAT DEFAULT 0.10,
                        max_daily_loss FLOAT DEFAULT 0.10,

                        -- 执行配置
                        auto_execute BOOLEAN DEFAULT false,
                        require_confirmation BOOLEAN DEFAULT true,
                        min_confidence FLOAT DEFAULT 0.6,

                        -- 学习配置
                        learning_enabled BOOLEAN DEFAULT true,
                        optimization_target VARCHAR(20) DEFAULT 'sharpe',
                        training_frequency VARCHAR(20) DEFAULT 'weekly',

                        -- 状态
                        status VARCHAR(20) DEFAULT 'draft',
                        account_id INTEGER REFERENCES accounts(id),
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        activated_at TIMESTAMP,
                        last_executed_at TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    );
                    """
                )
            )
            print("✅ Created table ai_strategies")

        # 3) Create strategy_memories table
        if not table_exists(inspector, "strategy_memories"):
            conn.execute(
                text(
                    """
                    CREATE TABLE strategy_memories (
                        id SERIAL PRIMARY KEY,
                        strategy_id VARCHAR(50) REFERENCES ai_strategies(strategy_id),

                        -- 统计摘要
                        total_trades INTEGER DEFAULT 0,
                        win_rate FLOAT DEFAULT 0,
                        avg_profit FLOAT DEFAULT 0,
                        avg_loss FLOAT DEFAULT 0,
                        sharpe_ratio FLOAT DEFAULT 0,
                        max_drawdown FLOAT DEFAULT 0,

                        -- 按市场状态的表现
                        performance_by_regime JSONB DEFAULT '{}',

                        -- 成功和失败案例
                        successful_patterns JSONB DEFAULT '[]',
                        failed_patterns JSONB DEFAULT '[]',
                        key_lessons TEXT[] DEFAULT '{}',

                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    );
                    """
                )
            )
            print("✅ Created table strategy_memories")

        # 4) Create strategy_trades table
        if not table_exists(inspector, "strategy_trades"):
            conn.execute(
                text(
                    """
                    CREATE TABLE strategy_trades (
                        id SERIAL PRIMARY KEY,
                        strategy_id VARCHAR(50) REFERENCES ai_strategies(strategy_id),

                        -- 交易信息
                        symbol VARCHAR(20) NOT NULL,
                        side VARCHAR(10) NOT NULL,
                        entry_price FLOAT NOT NULL,
                        exit_price FLOAT,
                        position_size FLOAT NOT NULL,
                        leverage FLOAT DEFAULT 1,

                        -- 决策上下文
                        decision_context JSONB,
                        signal_context JSONB,
                        ai_reasoning TEXT,

                        -- 结果
                        pnl FLOAT,
                        pnl_pct FLOAT,
                        holding_period INTEGER,

                        -- 质量评分
                        decision_quality_score FLOAT,
                        execution_quality_score FLOAT,

                        -- 状态
                        status VARCHAR(20) DEFAULT 'open',
                        opened_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        closed_at TIMESTAMP
                    );
                    """
                )
            )
            print("✅ Created table strategy_trades")

        # 5) Create prompt_training_records table
        if not table_exists(inspector, "prompt_training_records"):
            conn.execute(
                text(
                    """
                    CREATE TABLE prompt_training_records (
                        id SERIAL PRIMARY KEY,
                        strategy_id VARCHAR(50) REFERENCES ai_strategies(strategy_id),
                        base_prompt_id INTEGER REFERENCES prompt_templates(id),
                        optimized_prompt_id INTEGER REFERENCES prompt_templates(id),
                        training_metrics JSONB,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    );
                    """
                )
            )
            print("✅ Created table prompt_training_records")

        # 6) Create signal_performance_history table
        if not table_exists(inspector, "signal_performance_history"):
            conn.execute(
                text(
                    """
                    CREATE TABLE signal_performance_history (
                        id SERIAL PRIMARY KEY,
                        signal_id INTEGER REFERENCES signal_pools(id),
                        strategy_id VARCHAR(50) REFERENCES ai_strategies(strategy_id),
                        period_start TIMESTAMP NOT NULL,
                        period_end TIMESTAMP NOT NULL,

                        -- 性能指标
                        total_triggers INTEGER DEFAULT 0,
                        successful_triggers INTEGER DEFAULT 0,
                        win_rate FLOAT DEFAULT 0,
                        avg_profit FLOAT DEFAULT 0,
                        avg_loss FLOAT DEFAULT 0,
                        sharpe_ratio FLOAT DEFAULT 0,

                        -- 市场状态关联
                        market_regime VARCHAR(50),
                        regime_confidence FLOAT,

                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    );
                    """
                )
            )
            print("✅ Created table signal_performance_history")

        # 7) Create indexes if missing
        inspector = inspect(engine)  # refresh metadata

        index_definitions = [
            ("ai_strategies", "idx_ai_strategies_account", "account_id"),
            ("ai_strategies", "idx_ai_strategies_status", "status"),
            ("strategy_trades", "idx_strategy_trades_strategy_id", "strategy_id"),
            ("strategy_trades", "idx_strategy_trades_symbol", "symbol"),
            ("strategy_trades", "idx_strategy_trades_opened_at", "opened_at"),
            ("prompt_training_records", "idx_prompt_training_records_strategy", "strategy_id"),
            ("signal_performance_history", "idx_signal_performance_history_signal", "signal_id"),
        ]

        for table_name, index_name, column in index_definitions:
            if table_exists(inspector, table_name) and not index_exists(inspector, table_name, index_name):
                conn.execute(text(f"CREATE INDEX {index_name} ON {table_name} ({column})"))
                print(f"✅ Created index {index_name} on {table_name}({column})")


# Alias for direct execution
main = upgrade


if __name__ == "__main__":
    upgrade()
