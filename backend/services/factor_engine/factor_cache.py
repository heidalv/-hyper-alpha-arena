"""
ATAS V2 因子计算引擎 - 因子缓存

提供Redis和内存两级缓
"""
import json
import pickle
import hashlib
from typing import Optional, Any
from datetime import datetime, timedelta
import logging

import pandas as pd

logger = logging.getLogger(__name__)


class FactorCache:
    """
    因子缓存管理
    
    支持:
    - 内存缓存(快
    - Redis缓存(持久
    - 数据库缓长期存储)
    """
    
    def __init__(
        self, 
        redis_client=None,
        db_session=None,
        memory_max_size: int = 1000,
        enable_memory: bool = True,
        enable_redis: bool = True,
        enable_db: bool = False
    ):
        """
        初始化缓存管理器
        
        Args:
            redis_client: Redis客户
            db_session: 数据库会
            memory_max_size: 内存缓存最大条目数
            enable_memory: 是否启用内存缓存
            enable_redis: 是否启用Redis缓存
            enable_db: 是否启用数据库缓
        """
        self.redis_client = redis_client
        self.db_session = db_session
        self.memory_max_size = memory_max_size
        
        self.enable_memory = enable_memory
        self.enable_redis = enable_redis and redis_client is not None
        self.enable_db = enable_db and db_session is not None
        
        # 内存缓存 (LRU)
        self._memory_cache = {}
        self._access_times = {}
        
        logger.info(
            f"FactorCache initialized: "
            f"memory={enable_memory}, redis={self.enable_redis}, db={self.enable_db}"
        )
    
    def _serialize_value(self, value: Any) -> bytes:
        """
        序列化
        
        Args:
            value: 要序列化的
            
        Returns:
            序列化后的字节数
        """
        if isinstance(value, pd.Series):
            # 序列化为JSON（更通用。

            data = {
                'type': 'series',
                'data': value.to_dict(),
                'index': value.index.tolist() if hasattr(value.index, 'tolist') else list(value.index),
                'name': value.name
            }
            return json.dumps(data).encode('utf-8')
        elif isinstance(value, pd.DataFrame):
            data = {
                'type': 'dataframe',
                'data': value.to_dict('list'),
                'index': value.index.tolist() if hasattr(value.index, 'tolist') else list(value.index),
                'columns': value.columns.tolist()
            }
            return json.dumps(data).encode('utf-8')
        else:
            # 使用pickle序列化其他类。

            return pickle.dumps(value)
    
    def _deserialize_value(self, data: bytes) -> Any:
        """
        反序列化
        
        Args:
            data: 序列化的字节数据
            
        Returns:
            反序列化后的
        """
        try:
            # 尝试JSON反序列化
            obj = json.loads(data.decode('utf-8'))
            
            if isinstance(obj, dict) and 'type' in obj:
                if obj['type'] == 'series':
                    return pd.Series(
                        obj['data'],
                        index=obj['index'],
                        name=obj.get('name')
                    )
                elif obj['type'] == 'dataframe':
                    return pd.DataFrame(
                        obj['data'],
                        index=obj['index'],
                        columns=obj['columns']
                    )
            
            return obj
        except Exception:
            # 回退到pickle
            return pickle.loads(data)
    
    def _evict_memory_cache(self):
        """清理内存缓存（LRU）。
        """
        if len(self._memory_cache) >= self.memory_max_size:
            # 找出最久未访问的键
            oldest_key = min(self._access_times, key=self._access_times.get)
            del self._memory_cache[oldest_key]
            del self._access_times[oldest_key]
    
    def get(self, cache_key: str) -> Optional[Any]:
        """
        获取缓存。
        
        Args:
            cache_key: 缓存。

            
        Returns:
            缓存的值，如果不存在返回None
        """
        # 1. 尝试内存缓存
        if self.enable_memory and cache_key in self._memory_cache:
            self._access_times[cache_key] = datetime.now()
            logger.debug(f"Cache HIT (memory): {cache_key}")
            return self._memory_cache[cache_key]
        
        # 2. 尝试Redis缓存
        if self.enable_redis:
            try:
                data = self.redis_client.get(f"factor:{cache_key}")
                if data:
                    value = self._deserialize_value(data)
                    
                    # 回填内存缓存
                    if self.enable_memory:
                        self._evict_memory_cache()
                        self._memory_cache[cache_key] = value
                        self._access_times[cache_key] = datetime.now()
                    
                    logger.debug(f"Cache HIT (redis): {cache_key}")
                    return value
            except Exception as e:
                logger.warning(f"Redis cache get failed: {e}")
        
        # 3. 尝试数据库缓。

        if self.enable_db:
            try:
                from backend.database.models import ATASFactorCache
                
                record = self.db_session.query(ATASFactorCache).filter(
                    ATASFactorCache.cache_key == cache_key,
                    ATASFactorCache.expires_at > datetime.now()
                ).first()
                
                if record:
                    value = self._deserialize_value(record.value)
                    
                    # 回填上层缓存
                    if self.enable_redis:
                        self.set(cache_key, value, ttl=record.cache_ttl)
                    
                    logger.debug(f"Cache HIT (db): {cache_key}")
                    return value
            except Exception as e:
                logger.warning(f"DB cache get failed: {e}")
        
        logger.debug(f"Cache MISS: {cache_key}")
        return None
    
    def set(
        self, 
        cache_key: str, 
        value: Any, 
        ttl: int = 3600,
        save_to_db: bool = False
    ) -> bool:
        """
        设置缓存。

        
        Args:
            cache_key: 缓存。

            value: 要缓存的。

            ttl: 过期时间(。

            save_to_db: 是否同时保存到数据库
            
        Returns:
            是否成功
        """
        try:
            # 1. 设置内存缓存
            if self.enable_memory:
                self._evict_memory_cache()
                self._memory_cache[cache_key] = value
                self._access_times[cache_key] = datetime.now()
            
            # 序列。

            data = self._serialize_value(value)
            
            # 2. 设置Redis缓存
            if self.enable_redis:
                try:
                    self.redis_client.setex(
                        f"factor:{cache_key}",
                        ttl,
                        data
                    )
                except Exception as e:
                    logger.warning(f"Redis cache set failed: {e}")
            
            # 3. 设置数据库缓。

            if self.enable_db and save_to_db:
                try:
                    from backend.database.models import ATASFactorCache
                    
                    record = ATASFactorCache(
                        cache_key=cache_key,
                        factor_id=cache_key.split('_')[1] if '_' in cache_key else 'unknown',
                        symbol=cache_key.split('_')[0] if '_' in cache_key else 'unknown',
                        timeframe='1d',  # TODO: 从cache_key解析
                        value=data,
                        calculated_at=datetime.now(),
                        expires_at=datetime.now() + timedelta(seconds=ttl)
                    )
                    
                    self.db_session.merge(record)
                    self.db_session.commit()
                except Exception as e:
                    logger.warning(f"DB cache set failed: {e}")
                    self.db_session.rollback()
            
            logger.debug(f"Cache SET: {cache_key} (ttl={ttl}s)")
            return True
            
        except Exception as e:
            logger.error(f"Cache set error: {e}")
            return False
    
    def delete(self, cache_key: str) -> bool:
        """
        删除缓存
        
        Args:
            cache_key: 缓存。

            
        Returns:
            是否成功
        """
        success = False
        
        # 删除内存缓存
        if cache_key in self._memory_cache:
            del self._memory_cache[cache_key]
            del self._access_times[cache_key]
            success = True
        
        # 删除Redis缓存
        if self.enable_redis:
            try:
                self.redis_client.delete(f"factor:{cache_key}")
                success = True
            except Exception as e:
                logger.warning(f"Redis cache delete failed: {e}")
        
        # 删除数据库缓。

        if self.enable_db:
            try:
                from backend.database.models import ATASFactorCache
                
                self.db_session.query(ATASFactorCache).filter(
                    ATASFactorCache.cache_key == cache_key
                ).delete()
                self.db_session.commit()
                success = True
            except Exception as e:
                logger.warning(f"DB cache delete failed: {e}")
                self.db_session.rollback()
        
        return success
    
    def clear(self, pattern: Optional[str] = None) -> int:
        """
        清空缓存
        
        Args:
            pattern: 匹配模式（可选），如 "BTCUSDT_*"
            
        Returns:
            清除的缓存条目数
        """
        count = 0
        
        # 清空内存缓存
        if pattern:
            keys_to_delete = [k for k in self._memory_cache if self._match_pattern(k, pattern)]
            for key in keys_to_delete:
                del self._memory_cache[key]
                del self._access_times[key]
                count += 1
        else:
            count += len(self._memory_cache)
            self._memory_cache.clear()
            self._access_times.clear()
        
        # 清空Redis缓存
        if self.enable_redis:
            try:
                if pattern:
                    keys = self.redis_client.keys(f"factor:{pattern}")
                else:
                    keys = self.redis_client.keys("factor:*")
                
                if keys:
                    self.redis_client.delete(*keys)
                    count += len(keys)
            except Exception as e:
                logger.warning(f"Redis cache clear failed: {e}")
        
        logger.info(f"Cleared {count} cache entries")
        return count
    
    def _match_pattern(self, key: str, pattern: str) -> bool:
        """简单的通配符匹配。
        """
        import re
        regex = pattern.replace('*', '.*').replace('?', '.')
        return re.match(regex, key) is not None
    
    def stats(self) -> dict:
        """
        获取缓存统计信息
        
        Returns:
            统计信息字典
        """
        stats = {
            'memory_size': len(self._memory_cache),
            'memory_max_size': self.memory_max_size,
        }
        
        if self.enable_redis:
            try:
                redis_keys = self.redis_client.keys("factor:*")
                stats['redis_size'] = len(redis_keys)
            except Exception:
                stats['redis_size'] = -1
        
        return stats
