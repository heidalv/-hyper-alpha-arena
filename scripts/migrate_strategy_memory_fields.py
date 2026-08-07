"""
为 strategy_memories 表新增减仓记忆字段
运行方式: python scripts/migrate_strategy_memory_fields.py
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.database.connection import engine
from sqlalchemy import text


def migrate():
    with engine.connect() as conn:
        # 检查并添加 partial_pnl 字段
        try:
            conn.execute(text("ALTER TABLE strategy_memories ADD COLUMN partial_pnl FLOAT DEFAULT 0.0"))
            print("✓ 添加 partial_pnl 字段")
        except Exception as e:
            if "duplicate" in str(e).lower() or "already exists" in str(e).lower():
                print("⚠ partial_pnl 字段已存在，跳过")
            else:
                print(f"✗ partial_pnl: {e}")

        # 检查并添加 partial_close_count 字段
        try:
            conn.execute(text("ALTER TABLE strategy_memories ADD COLUMN partial_close_count INTEGER DEFAULT 0"))
            print("✓ 添加 partial_close_count 字段")
        except Exception as e:
            if "duplicate" in str(e).lower() or "already exists" in str(e).lower():
                print("⚠ partial_close_count 字段已存在，跳过")
            else:
                print(f"✗ partial_close_count: {e}")

        # 检查并添加 last_reduce_at 字段
        try:
            conn.execute(text("ALTER TABLE strategy_memories ADD COLUMN last_reduce_at TIMESTAMP"))
            print("✓ 添加 last_reduce_at 字段")
        except Exception as e:
            if "duplicate" in str(e).lower() or "already exists" in str(e).lower():
                print("⚠ last_reduce_at 字段已存在，跳过")
            else:
                print(f"✗ last_reduce_at: {e}")

        # 创建联合索引（如果不存在）
        try:
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_ai_strategies_symbol_tier "
                "ON ai_strategies (primary_symbol, timeframe_tier)"
            ))
            print("✓ 创建联合索引 ix_ai_strategies_symbol_tier")
        except Exception as e:
            print(f"⚠ 索引: {e}")

        conn.commit()
        print("\n迁移完成!")


if __name__ == "__main__":
    migrate()
