
import sys
import os
from datetime import datetime
from sqlalchemy import create_engine, desc
from sqlalchemy.orm import sessionmaker

# 添加 backend 目录到路径
# Docker 容器内的 backend 路径是 /app/backend
sys.path.append('/app/backend')

# 手动指定数据库URL，避免导入问题
# 优先级：环境变量 > backend 数据库配置 > 硬编码默认（仅用于 Docker 本地开发）
def _resolve_database_url() -> str:
    # 1. 环境变量优先
    env_url = os.getenv("DATABASE_URL")
    if env_url:
        return env_url

    # 2. 尝试从项目配置中读取
    try:
        from backend.database.connection import DATABASE_URL as _cfg_url
        if _cfg_url:
            return _cfg_url
    except Exception:
        pass

    # 3. 兜底：SQLite（本地开发默认）
    return "sqlite:///../backend/trading.db"

DATABASE_URL = _resolve_database_url()

# 直接导入模型，如果这也不行就直接写 SQL
try:
    from database.models import AIDecisionLog
except ImportError:
    # 如果导入失败，我们尝试从当前目录导入（假设脚本在 /app/backend 下运行）
    sys.path.append('/app')
    from backend.database.models import AIDecisionLog

# 初始化数据库连接
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
db = SessionLocal()

def force_close_zombies():
    """
    针对特定 ID 列表，强制插入平仓记录，绕过所有逻辑检查。
    """
    # 用户提供的顽固僵尸 ID 列表
    target_ids = [271, 187, 134, 133, 79, 55, 13, 1]
    
    print(f"========== 开始强制清理僵尸持仓 (Target IDs: {target_ids}) ==========")
    
    try:
        # 1. 获取这些 ID 对应的开仓记录
        target_logs = db.query(AIDecisionLog).filter(AIDecisionLog.id.in_(target_ids)).all()
        
        if not target_logs:
            print("❌ 未在数据库中找到任何指定的 ID 记录。请确认 ID 是否正确。")
            return

        print(f"✅ 找到 {len(target_logs)} 条目标记录，准备逐一击破...")
        
        count = 0
        for log in target_logs:
            print(f"\n[处理 ID: {log.id}] {log.symbol} {log.operation} @ {log.decision_time}")
            
            # 2. 检查是否已经有对应的平仓记录（即使是 executed=false 也要检查）
            # 注意：这里的查询逻辑与后端接口保持一致，只检查 symbol 和 time
            existing_closes = db.query(AIDecisionLog).filter(
                AIDecisionLog.account_id == log.account_id,
                AIDecisionLog.symbol == log.symbol,
                AIDecisionLog.operation == 'close',
                AIDecisionLog.decision_time > log.decision_time
            ).all()
            
            if existing_closes:
                print(f"  ⚠️ 警告: 已发现 {len(existing_closes)} 条潜在平仓记录:")
                for c in existing_closes:
                    print(f"    - Close ID: {c.id}, Executed: {c.executed}, Time: {c.decision_time}")
                
                # 如果存在 executed='true' 的记录，理论上前端应该已经过滤掉了
                # 但既然用户说还在，我们就强制再加一条“完美”的平仓记录
                print("  -> 尽管存在记录，但用户反馈无效。强制插入新的平仓记录...")
            
            # 3. 构造完美的平仓记录
            # 关键点：executed="true"，时间必须晚于开仓时间
            # 注意：移除 decision_type, execution_time 参数
            # 重新加回 target_portion_of_balance 但改名为 target_portion
            # 补充 total_balance (NOT NULL)
            close_log = AIDecisionLog(
                account_id=log.account_id,
                symbol=log.symbol,
                operation="close",
                target_portion=0.0, # 修复字段名
                total_balance=0.0,  # 补充字段: NOT NULL
                reason=f"FORCE_CLEAN_ZOMBIE: Ref ID {log.id}",
                executed="true",
                # execution_time=datetime.now(), 
                decision_time=datetime.now(),
                decision_snapshot=log.decision_snapshot
            )
            
            db.add(close_log)
            print(f"  ✅ 已插入强制平仓记录 for ID {log.id}")
            count += 1
            
        # 4. 提交事务
        db.commit()
        print(f"\n🎉 成功处理 {count} 条记录。事务已提交。")
        print("请刷新前端页面查看效果。")
        
    except Exception as e:
        print(f"❌ 发生严重错误: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    force_close_zombies()
