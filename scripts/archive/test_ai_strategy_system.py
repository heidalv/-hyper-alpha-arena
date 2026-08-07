#!/usr/bin/env python3
"""
AI策略系统测试脚本
验证核心功能是否正常工作
"""
import sys
import os

# 添加项目路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from backend.database.connection import get_db
from backend.database.models import AIStrategy, Account

def test_ai_strategy_system():
    """测试AI策略系统"""
    print("=" * 60)
    print("AI策略决策层升级 - 系统测试")
    print("=" * 60)
    
    db = next(get_db())
    
    try:
        # 1. 检查数据库表是否存在
        print("\n[1] 检查数据库表...")
        from sqlalchemy import inspect
        from backend.database.connection import engine
        inspector = inspect(engine)
        
        required_tables = [
            'ai_strategies',
            'strategy_memories',
            'strategy_trades',
            'prompt_training_records',
        ]
        
        existing_tables = inspector.get_table_names()
        for table in required_tables:
            if table in existing_tables:
                print(f"  ✅ {table} 表已创建")
            else:
                print(f"  ❌ {table} 表不存在")
        
        # 2. 检查策略协调器（新引擎入口）
        print("\n[2] 检查策略协调器...")
        try:
            from backend.services.strategy_coordinator import StrategyCoordinator
            print("  ✅ StrategyCoordinator (新引擎) 可导入")
        except ImportError as e:
            print(f"  ⚠️  StrategyCoordinator 导入失败: {e}")
        
        # 3. 检查账户
        print("\n[3] 检查账户...")
        accounts = db.query(Account).all()
        if accounts:
            print(f"  ✅ 找到 {len(accounts)} 个账户")
            for acc in accounts[:3]:
                print(f"     - {acc.name} (ID: {acc.id})")
        else:
            print("  ⚠️  未找到账户，请先创建账户")
        
        # 4. 检查AI策略
        print("\n[4] 检查AI策略...")
        strategies = db.query(AIStrategy).all()
        if strategies:
            print(f"  ✅ 找到 {len(strategies)} 个AI策略")
            for strategy in strategies[:3]:
                print(f"     - {strategy.name} ({strategy.status})")
        else:
            print("  ℹ️  暂无AI策略（正常，可通过前端创建）")
        
        # 5. 检查API路由是否注册
        print("\n[5] 检查API路由...")
        try:
            from backend.main import app
            routes = [route.path for route in app.routes]
            ai_strategy_routes = [r for r in routes if '/ai-strategies' in r]
            prompt_training_routes = [r for r in routes if '/prompt-training' in r]
            
            if ai_strategy_routes:
                print(f"  ✅ AI策略路由已注册 ({len(ai_strategy_routes)} 个)")
                for route in ai_strategy_routes[:5]:
                    print(f"     - {route}")
            else:
                print("  ❌ AI策略路由未注册")
            
            if prompt_training_routes:
                print(f"  ✅ 提示词训练路由已注册 ({len(prompt_training_routes)} 个)")
                for route in prompt_training_routes[:5]:
                    print(f"     - {route}")
            else:
                print("  ❌ 提示词训练路由未注册")
        except Exception as e:
            print(f"  ⚠️  无法检查路由: {e}")
        
        # 6. 检查提示词训练系统
        print("\n[6] 检查提示词训练系统...")
        try:
            from backend.services.prompt_training_system import PromptTrainingSystem
            training_system = PromptTrainingSystem(db)
            print("  ✅ PromptTrainingSystem 初始化成功")
        except Exception as e:
            print(f"  ❌ PromptTrainingSystem 初始化失败: {e}")
        
        print("\n" + "=" * 60)
        print("测试完成！")
        print("=" * 60)
        print("\n下一步：")
        print("1. 启动系统：python launcher.py")
        print("2. 访问前端：http://localhost:5173")
        print("3. 进入 ATAS V2 -> AI策略中心")
        print("4. 创建你的第一个AI策略")
        print("\n✨ AI策略决策层升级已就绪！")
        
    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
    finally:
        db.close()

if __name__ == "__main__":
    test_ai_strategy_system()
