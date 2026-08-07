"""
数据库迁移脚本：添加可视化策略相关表
创建日期：2026-02-02
"""

from sqlalchemy import create_engine, text
import os
import sys

# 添加项目根目录到Python路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.database.connection import get_db_url


def upgrade():
    """升级数据库：创建可视化策略相关表"""
    
    engine = create_engine(get_db_url())
    
    with engine.connect() as conn:
        # 开始事务
        trans = conn.begin()
        
        try:
            print("开始创建可视化策略相关表...")
            
            # 1. 创建 visual_strategies 表
            print("1. 创建 visual_strategies 表...")
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS visual_strategies (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id),
                    
                    -- 策略基本信息
                    name VARCHAR(200) NOT NULL,
                    description TEXT,
                    version INTEGER NOT NULL DEFAULT 1,
                    
                    -- 策略内容
                    nodes JSONB NOT NULL,
                    edges JSONB NOT NULL,
                    generated_code TEXT,
                    
                    -- 状态
                    status VARCHAR(20) NOT NULL DEFAULT 'draft',
                    
                    -- 性能指标
                    backtest_result JSONB,
                    live_performance JSONB,
                    
                    -- 时间戳
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
            """))
            
            # 创建索引
            conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_visual_strategies_user_id 
                ON visual_strategies(user_id);
            """))
            
            conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_visual_strategies_status 
                ON visual_strategies(status);
            """))
            
            print("   ✅ visual_strategies 表创建成功")
            
            # 2. 创建 strategy_executions 表
            print("2. 创建 strategy_executions 表...")
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS strategy_executions (
                    id SERIAL PRIMARY KEY,
                    strategy_id INTEGER NOT NULL REFERENCES visual_strategies(id) ON DELETE CASCADE,
                    
                    -- 执行类型
                    execution_type VARCHAR(20) NOT NULL,
                    
                    -- 执行参数
                    start_time TIMESTAMP,
                    end_time TIMESTAMP,
                    symbols JSONB,
                    config JSONB,
                    
                    -- 执行结果
                    status VARCHAR(20) NOT NULL DEFAULT 'pending',
                    result JSONB,
                    logs TEXT,
                    error_message TEXT,
                    
                    -- 性能指标
                    total_trades INTEGER,
                    win_rate FLOAT,
                    profit_loss FLOAT,
                    sharpe_ratio FLOAT,
                    max_drawdown FLOAT,
                    
                    -- 时间戳
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
            """))
            
            # 创建索引
            conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_strategy_executions_strategy_id 
                ON strategy_executions(strategy_id);
            """))
            
            conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_strategy_executions_type 
                ON strategy_executions(execution_type);
            """))
            
            conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_strategy_executions_status 
                ON strategy_executions(status);
            """))
            
            print("   ✅ strategy_executions 表创建成功")
            
            # 3. 创建 strategy_node_templates 表
            print("3. 创建 strategy_node_templates 表...")
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS strategy_node_templates (
                    id SERIAL PRIMARY KEY,
                    
                    -- 节点基本信息
                    category VARCHAR(50) NOT NULL,
                    name VARCHAR(100) NOT NULL,
                    type VARCHAR(50) NOT NULL UNIQUE,
                    icon VARCHAR(50),
                    description TEXT,
                    
                    -- 配置Schema
                    config_schema JSONB,
                    
                    -- 代码生成模板
                    code_template TEXT,
                    
                    -- 状态
                    is_active VARCHAR(10) NOT NULL DEFAULT 'true',
                    
                    -- 使用统计
                    usage_count INTEGER NOT NULL DEFAULT 0,
                    
                    -- 时间戳
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
            """))
            
            # 创建索引
            conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_strategy_node_templates_category 
                ON strategy_node_templates(category);
            """))
            
            conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_strategy_node_templates_type 
                ON strategy_node_templates(type);
            """))
            
            print("   ✅ strategy_node_templates 表创建成功")
            
            # 提交事务
            trans.commit()
            print("\n✅ 所有表创建成功！")
            print("\n创建的表:")
            print("  1. visual_strategies - 可视化策略表")
            print("  2. strategy_executions - 策略执行历史表")
            print("  3. strategy_node_templates - 策略节点模板库")
            
        except Exception as e:
            # 回滚事务
            trans.rollback()
            print(f"\n❌ 迁移失败: {str(e)}")
            raise


def downgrade():
    """降级数据库：删除可视化策略相关表"""
    
    engine = create_engine(get_db_url())
    
    with engine.connect() as conn:
        trans = conn.begin()
        
        try:
            print("开始删除可视化策略相关表...")
            
            # 按照依赖顺序删除表
            conn.execute(text("DROP TABLE IF EXISTS strategy_executions CASCADE;"))
            print("  ✅ strategy_executions 表已删除")
            
            conn.execute(text("DROP TABLE IF EXISTS strategy_node_templates CASCADE;"))
            print("  ✅ strategy_node_templates 表已删除")
            
            conn.execute(text("DROP TABLE IF EXISTS visual_strategies CASCADE;"))
            print("  ✅ visual_strategies 表已删除")
            
            trans.commit()
            print("\n✅ 所有表已删除！")
            
        except Exception as e:
            trans.rollback()
            print(f"\n❌ 降级失败: {str(e)}")
            raise


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='可视化策略数据库迁移')
    parser.add_argument('action', choices=['upgrade', 'downgrade'], help='升级或降级数据库')
    
    args = parser.parse_args()
    
    if args.action == 'upgrade':
        upgrade()
    else:
        downgrade()
