"""
ATAS V2 - 因子缓存管理器

提供缓存预热、批量管理、失效策略等高级功能
"""
import asyncio
from typing import List, Dict, Optional, Set
from datetime import datetime, timedelta
import logging

from .factor_cache import FactorCache
from .factor_calculator import FactorCalculator
from .factor_loader import get_factor_loader

logger = logging.getLogger(__name__)


class FactorCacheManager:
    """因子缓存管理器 - 提供缓存预热和批量管理"""
    
    def __init__(
        self,
        cache: FactorCache,
        calculator: FactorCalculator
    ):
        self.cache = cache
        self.calculator = calculator
        self.loader = get_factor_loader()
        
        # 预热任务跟踪
        self.warming_tasks: Dict[str, asyncio.Task] = {}
        self.warming_status: Dict[str, Dict] = {}
    
    async def warm_up_factors(
        self,
        factor_ids: List[str],
        symbols: List[str],
        timeframes: List[str] = ['1d'],
        lookback_days: int = 30
    ) -> Dict[str, int]:
        """
        批量预热因子缓存
        
        Args:
            factor_ids: 要预热的因子ID列表
            symbols: 交易对列表
            timeframes: 时间周期列表
            lookback_days: 回溯天数
            
        Returns:
            预热统计 {状态: 数量}
        """
        logger.info(f"Starting cache warm-up for {len(factor_ids)} factors, {len(symbols)} symbols")
        
        stats = {'success': 0, 'failed': 0, 'skipped': 0}
        
        tasks = []
        for symbol in symbols:
            for timeframe in timeframes:
                task = self._warm_up_symbol(
                    factor_ids,
                    symbol,
                    timeframe,
                    lookback_days
                )
                tasks.append(task)
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        for result in results:
            if isinstance(result, Exception):
                stats['failed'] += 1
            elif result:
                stats['success'] += result
            else:
                stats['skipped'] += 1
        
        logger.info(f"Cache warm-up complete: {stats}")
        return stats
    
    async def _warm_up_symbol(
        self,
        factor_ids: List[str],
        symbol: str,
        timeframe: str,
        lookback_days: int
    ) -> int:
        """预热单个交易对的因子"""
        try:
            # 获取历史数据（实际应从数据库获取）
            data = await self._fetch_historical_data(symbol, timeframe, lookback_days)
            
            if data is None or len(data) == 0:
                return 0
            
            # 计算因子
            results = self.calculator.calculate(
                factor_ids=factor_ids,
                data=data,
                symbol=symbol,
                timeframe=timeframe,
                use_cache=True  # 自动写入缓存
            )
            
            return len(results)
            
        except Exception as e:
            logger.error(f"Warm-up failed for {symbol}/{timeframe}: {str(e)}")
            return 0
    
    async def _fetch_historical_data(self, symbol: str, timeframe: str, days: int):
        """获取历史数据（模拟）"""
        # TODO: 实际实现从数据库获取历史数据
        # 这里返回None表示数据不可用
        return None
    
    def invalidate_pattern(self, pattern: str):
        """
        按模式批量失效缓存
        
        Args:
            pattern: 缓存键模式，例如 "BTCUSDT_*" 或 "*_rsi_14"
        """
        if not self.cache.enable_redis:
            logger.warning("Redis not enabled, pattern invalidation not supported")
            return
        
        try:
            # 使用Redis SCAN命令查找匹配的键
            cursor = 0
            deleted_count = 0
            
            while True:
                cursor, keys = self.cache.redis_client.scan(
                    cursor,
                    match=f"factor:{pattern}",
                    count=100
                )
                
                if keys:
                    self.cache.redis_client.delete(*keys)
                    deleted_count += len(keys)
                
                if cursor == 0:
                    break
            
            logger.info(f"Invalidated {deleted_count} cache entries matching '{pattern}'")
            
        except Exception as e:
            logger.error(f"Pattern invalidation failed: {str(e)}")
    
    def invalidate_by_symbol(self, symbol: str):
        """失效指定交易对的所有缓存"""
        self.invalidate_pattern(f"{symbol}_*")
    
    def invalidate_by_factor(self, factor_id: str):
        """失效指定因子的所有缓存"""
        self.invalidate_pattern(f"*_{factor_id}")
    
    def get_cache_stats(self) -> Dict:
        """获取缓存统计信息"""
        stats = {
            'memory_size': len(self.cache._memory_cache) if self.cache.enable_memory else 0,
            'memory_max': self.cache.memory_max_size,
            'memory_enabled': self.cache.enable_memory,
            'redis_enabled': self.cache.enable_redis,
            'db_enabled': self.cache.enable_db,
            'warming_tasks': len(self.warming_tasks)
        }
        
        # Redis统计
        if self.cache.enable_redis:
            try:
                info = self.cache.redis_client.info('keyspace')
                # 解析键空间信息
                if 'db0' in info:
                    db_info = info['db0']
                    stats['redis_keys'] = db_info.get('keys', 0)
                else:
                    stats['redis_keys'] = 0
            except Exception:
                stats['redis_keys'] = 'unknown'
        
        return stats
    
    def schedule_periodic_cleanup(self, interval_hours: int = 24):
        """
        定期清理过期缓存
        
        Args:
            interval_hours: 清理间隔（小时）
        """
        async def cleanup_loop():
            while True:
                try:
                    await asyncio.sleep(interval_hours * 3600)
                    self._cleanup_expired()
                except Exception as e:
                    logger.error(f"Cleanup error: {str(e)}")
        
        task = asyncio.create_task(cleanup_loop())
        self.warming_tasks['cleanup'] = task
    
    def _cleanup_expired(self):
        """清理过期缓存"""
        if self.cache.enable_db:
            try:
                from database.models import ATASFactorCache
                
                expired_count = self.cache.db_session.query(ATASFactorCache).filter(
                    ATASFactorCache.expires_at < datetime.now()
                ).delete()
                
                self.cache.db_session.commit()
                logger.info(f"Cleaned up {expired_count} expired cache entries")
                
            except Exception as e:
                logger.error(f"DB cleanup failed: {str(e)}")
                self.cache.db_session.rollback()
    
    def preload_hot_factors(self, hot_factor_ids: List[str], symbols: List[str]):
        """
        预加载热门因子
        
        Args:
            hot_factor_ids: 热门因子ID列表
            symbols: 关注的交易对列表
        """
        asyncio.create_task(
            self.warm_up_factors(
                factor_ids=hot_factor_ids,
                symbols=symbols,
                timeframes=['1h', '4h', '1d'],
                lookback_days=7  # 热门因子只需要短期数据
            )
        )
