"""
AI策略系统性能优化建议和实施

包括：
1. 数据库查询优化
2. 缓存策略
3. 异步处理
4. 批量操作优化
"""
import logging
from typing import List, Dict, Any, Optional
from functools import lru_cache
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)


class PerformanceOptimizer:
    """性能优化工具类"""
    
    @staticmethod
    def optimize_database_queries():
        """数据库查询优化建议"""
        optimizations = {
            "索引优化": [
                "CREATE INDEX idx_ai_strategies_status ON ai_strategies(status);",
                "CREATE INDEX idx_ai_strategies_account ON ai_strategies(account_id);",
                "CREATE INDEX idx_strategy_memories_strategy ON strategy_memories(strategy_id);",
                "CREATE INDEX idx_prompt_training_strategy ON prompt_training_records(strategy_id);",
                "CREATE INDEX idx_prompt_training_status ON prompt_training_records(status);",
            ],
            "查询优化": [
                "使用 select_related/joinedload 预加载关联数据",
                "避免 N+1 查询问题",
                "使用 defer/only 只查询需要的字段",
                "对大结果集使用分页",
            ],
        }
        return optimizations
    
    @staticmethod
    @lru_cache(maxsize=128)
    def get_cached_strategy_config(strategy_id: str) -> Optional[Dict[str, Any]]:
        """缓存策略配置（示例）
        
        注意：实际使用时需要集成Redis或其他缓存系统
        """
        # 这里只是示例，实际应该从数据库加载并缓存
        logger.info(f"Loading strategy config for {strategy_id} (will be cached)")
        return None
    
    @staticmethod
    def batch_load_strategies(strategy_ids: List[str], db) -> Dict[str, Any]:
        """批量加载策略（避免循环查询）"""
        from backend.database.models import AIStrategy
        
        strategies = db.query(AIStrategy).filter(
            AIStrategy.strategy_id.in_(strategy_ids)
        ).all()
        
        return {s.strategy_id: s for s in strategies}
    
    @staticmethod
    def async_processing_recommendations():
        """异步处理建议"""
        return [
            "将提示词训练任务放入后台队列（Celery/RQ）",
            "异步执行策略决策，避免阻塞API请求",
            "使用消息队列处理大量策略执行任务",
            "异步更新策略记忆统计",
        ]
    
    @staticmethod
    def monitoring_recommendations():
        """监控建议"""
        return {
            "关键指标": [
                "策略执行延迟（P50/P95/P99）",
                "AI决策响应时间",
                "数据库查询耗时",
                "API请求成功率",
                "策略内存使用量",
            ],
            "告警阈值": {
                "策略执行失败率": "> 5%",
                "API响应时间": "> 3秒",
                "数据库连接池": "> 80%使用率",
                "策略记忆更新延迟": "> 5分钟",
            },
        }


class CacheManager:
    """缓存管理器（简化版本）
    
    生产环境建议使用Redis
    """
    
    def __init__(self):
        self._cache: Dict[str, tuple] = {}  # key -> (value, expire_time)
    
    def get(self, key: str) -> Optional[Any]:
        """获取缓存"""
        if key in self._cache:
            value, expire_time = self._cache[key]
            if datetime.now(timezone.utc) < expire_time:
                return value
            else:
                del self._cache[key]
        return None
    
    def set(self, key: str, value: Any, ttl_seconds: int = 300):
        """设置缓存"""
        expire_time = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
        self._cache[key] = (value, expire_time)
    
    def delete(self, key: str):
        """删除缓存"""
        if key in self._cache:
            del self._cache[key]
    
    def clear(self):
        """清空缓存"""
        self._cache.clear()


class QueryOptimizer:
    """查询优化器"""
    
    @staticmethod
    def optimized_strategy_list_query(db, filters: Dict[str, Any]):
        """优化的策略列表查询"""
        from backend.database.models import AIStrategy
        from sqlalchemy.orm import joinedload
        
        query = db.query(AIStrategy)
        
        # 应用过滤器
        if "status" in filters:
            query = query.filter(AIStrategy.status == filters["status"])
        if "account_id" in filters:
            query = query.filter(AIStrategy.account_id == filters["account_id"])
        
        # 预加载关联数据（如果需要）
        # query = query.options(joinedload(AIStrategy.memories))
        
        # 只查询必要的字段
        # query = query.options(defer(AIStrategy.prompt_variables))
        
        # 排序和分页
        query = query.order_by(AIStrategy.created_at.desc())
        
        return query
    
    @staticmethod
    def optimized_training_history_query(db, strategy_id: Optional[str] = None, limit: int = 50):
        """优化的训练历史查询"""
        from backend.database.models import PromptTrainingRecord
        
        query = db.query(PromptTrainingRecord)
        
        if strategy_id:
            query = query.filter(PromptTrainingRecord.strategy_id == strategy_id)
        
        # 只查询列表需要的字段
        query = query.with_entities(
            PromptTrainingRecord.id,
            PromptTrainingRecord.strategy_id,
            PromptTrainingRecord.status,
            PromptTrainingRecord.optimization_target,
            PromptTrainingRecord.sample_count,
            PromptTrainingRecord.created_at,
        )
        
        query = query.order_by(PromptTrainingRecord.created_at.desc()).limit(limit)
        
        return query


def apply_database_optimizations(db_engine):
    """应用数据库优化
    
    Args:
        db_engine: SQLAlchemy引擎
    """
    optimizer = PerformanceOptimizer()
    optimizations = optimizer.optimize_database_queries()
    
    print("=" * 60)
    print("数据库优化建议")
    print("=" * 60)
    
    print("\n1. 建议创建的索引：")
    for index_sql in optimizations["索引优化"]:
        print(f"   {index_sql}")
    
    print("\n2. 查询优化建议：")
    for suggestion in optimizations["查询优化"]:
        print(f"   - {suggestion}")
    
    print("\n3. 应用索引（需要手动确认）：")
    apply = input("是否现在应用这些索引？(y/N): ")
    
    if apply.lower() == 'y':
        try:
            with db_engine.begin() as conn:
                for index_sql in optimizations["索引优化"]:
                    try:
                        conn.execute(index_sql)
                        print(f"✓ 已创建: {index_sql}")
                    except Exception as e:
                        if "already exists" in str(e).lower():
                            print(f"  (已存在)")
                        else:
                            print(f"✗ 失败: {e}")
            print("\n✅ 索引优化完成！")
        except Exception as e:
            print(f"\n❌ 应用索引失败: {e}")
    else:
        print("\n跳过索引创建")


def generate_performance_report():
    """生成性能优化报告"""
    optimizer = PerformanceOptimizer()
    
    report = f"""
{'='*60}
AI策略系统性能优化报告
{'='*60}

1. 数据库优化
{'-'*60}
{optimizer.optimize_database_queries()}

2. 异步处理建议
{'-'*60}
{chr(10).join(f"  - {rec}" for rec in optimizer.async_processing_recommendations())}

3. 监控建议
{'-'*60}
关键指标：
{chr(10).join(f"  - {metric}" for metric in optimizer.monitoring_recommendations()["关键指标"])}

告警阈值：
{chr(10).join(f"  - {k}: {v}" for k, v in optimizer.monitoring_recommendations()["告警阈值"].items())}

4. 缓存策略建议
{'-'*60}
  - 策略配置缓存（TTL: 5分钟）
  - 提示词模板缓存（TTL: 10分钟）
  - 策略记忆统计缓存（TTL: 1分钟）
  - 使用Redis实现分布式缓存

5. 下一步行动
{'-'*60}
  1. 应用数据库索引优化
  2. 集成Redis缓存
  3. 实现异步任务队列
  4. 配置监控告警
  5. 进行压力测试

{'='*60}
"""
    
    return report


if __name__ == "__main__":
    # 生成并打印优化报告
    print(generate_performance_report())
    
    # 可选：应用数据库优化
    from backend.database.connection import engine
    apply_database_optimizations(engine)
