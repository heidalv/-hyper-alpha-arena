"""
ATAS V2 因子计算引擎 - 因子计算器

高性能因子批量计算引擎
支持: 向量化计算、多进程并行、缓存、增量计算
"""
import time
from typing import Dict, List, Optional, Any
from datetime import datetime
from concurrent.futures import ProcessPoolExecutor, as_completed
import logging

import pandas as pd
import numpy as np

from .factor_base import BaseFactor, FactorResult
from .factor_registry import registry
from .factor_cache import FactorCache

logger = logging.getLogger(__name__)


class FactorCalculator:
    """
    因子计算引擎
    
    核心功能:
    1. 批量计算多个因子
    2. 自动解析依赖关系
    3. 缓存管理
    4. 并行计算(可选)
    5. 增量计算(可选)
    """
    
    def __init__(
        self,
        cache: Optional[FactorCache] = None,
        parallel: bool = False,
        max_workers: int = 4,
        enable_incremental: bool = False
    ):
        """
        初始化计算引擎
        
        Args:
            cache: 因子缓存实例
            parallel: 是否启用并行计算
            max_workers: 并行工作进程数
            enable_incremental: 是否启用增量计算
        """
        self.cache = cache
        self.parallel = parallel
        self.max_workers = max_workers
        self.enable_incremental = enable_incremental
        
        self.registry = registry
        
        logger.info(
            f"FactorCalculator initialized: "
            f"parallel={parallel}, max_workers={max_workers}"
        )
    
    def calculate(
        self,
        factor_ids: List[str],
        data: pd.DataFrame,
        symbol: str,
        timeframe: str = '1d',
        params: Optional[Dict[str, Dict[str, Any]]] = None,
        use_cache: bool = True
    ) -> Dict[str, pd.Series]:
        """
        批量计算因子
        
        Args:
            factor_ids: 要计算的因子ID列表
            data: 市场数据DataFrame (必须包含timestamp索引)
            symbol: 交易对符号
            timeframe: 时间周期
            params: 各因子的参数 {factor_id: params_dict}
            use_cache: 是否使用缓存
            
        Returns:
            因子计算结果字典 {factor_id: result_series}
        """
        start_time = time.time()
        
        # 解析依赖关系，确定计算顺序
        try:
            ordered_factor_ids = self.registry.resolve_dependencies(factor_ids)
        except ValueError as e:
            logger.error(f"Failed to resolve dependencies: {e}")
            raise
        
        logger.info(
            f"Calculating {len(ordered_factor_ids)} factors "
            f"({len(factor_ids)} requested, {len(ordered_factor_ids) - len(factor_ids)} dependencies) "
            f"for {symbol} {timeframe}"
        )
        
        # 存储结果
        results = {}
        
        # 按顺序计算因子
        for factor_id in ordered_factor_ids:
            try:
                # 获取因子参数
                factor_params = params.get(factor_id) if params else None
                
                # 计算单个因子
                result = self._calculate_single_factor(
                    factor_id=factor_id,
                    data=data,
                    symbol=symbol,
                    timeframe=timeframe,
                    params=factor_params,
                    use_cache=use_cache,
                    dependency_results=results  # 传递已计算的依赖结果
                )
                
                results[factor_id] = result
                
            except Exception as e:
                logger.error(f"Failed to calculate factor {factor_id}: {e}", exc_info=True)
                # 继续计算其他因子
                results[factor_id] = pd.Series(np.nan, index=data.index, name=factor_id)
        
        # 只返回用户请求的因子
        final_results = {fid: results[fid] for fid in factor_ids if fid in results}
        
        elapsed = time.time() - start_time
        logger.info(
            f"Calculated {len(final_results)} factors in {elapsed:.2f}s "
            f"({len(data)} data points)"
        )
        
        return final_results
    
    def _calculate_single_factor(
        self,
        factor_id: str,
        data: pd.DataFrame,
        symbol: str,
        timeframe: str,
        params: Optional[Dict[str, Any]] = None,
        use_cache: bool = True,
        dependency_results: Optional[Dict[str, pd.Series]] = None
    ) -> pd.Series:
        """
        计算单个因子
        
        Args:
            factor_id: 因子ID
            data: 市场数据
            symbol: 交易对
            timeframe: 时间周期
            params: 因子参数
            use_cache: 是否使用缓存
            dependency_results: 依赖因子的计算结果
            
        Returns:
            因子值Series
        """
        # 生成缓存键
        cache_key = self._generate_cache_key(
            symbol, factor_id, timeframe, data.index[-1], params
        )
        
        # 尝试从缓存获取
        if use_cache and self.cache:
            cached_result = self.cache.get(cache_key)
            if cached_result is not None:
                logger.debug(f"Factor {factor_id} loaded from cache")
                return cached_result
        
        # 创建因子实例
        factor = self.registry.get(factor_id, params=params)
        
        # 验证数据
        try:
            factor.validate_data(data)
        except ValueError as e:
            logger.warning(f"Data validation failed for {factor_id}: {e}")
            return pd.Series(np.nan, index=data.index, name=factor_id)
        
        # 预处理数据
        processed_data = factor.preprocess_data(data)
        
        # 计算因子
        calc_start = time.time()
        
        try:
            result = factor.calculate(processed_data)
            
            # 后处理结果
            result = factor.postprocess_result(result)
            
            # 确保结果与原始数据对齐
            if len(result) != len(data):
                result = result.reindex(data.index)
            
            calc_time_ms = (time.time() - calc_start) * 1000
            
            logger.debug(
                f"Calculated {factor_id} in {calc_time_ms:.2f}ms "
                f"({len(data)} points)"
            )
            
            # 保存到缓存
            if use_cache and self.cache:
                metadata = factor.metadata
                ttl = metadata.cache_ttl if metadata.cache_enabled else 0
                
                if ttl > 0:
                    self.cache.set(cache_key, result, ttl=ttl)
            
            return result
            
        except Exception as e:
            logger.error(f"Failed to calculate {factor_id}: {e}", exc_info=True)
            return pd.Series(np.nan, index=data.index, name=factor_id)
    
    def _generate_cache_key(
        self,
        symbol: str,
        factor_id: str,
        timeframe: str,
        timestamp: datetime,
        params: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        生成缓存键
        
        Args:
            symbol: 交易对
            factor_id: 因子ID
            timeframe: 时间周期
            timestamp: 时间戳
            params: 因子参数
            
        Returns:
            缓存键字符串
        """
        ts_str = timestamp.strftime("%Y%m%d%H%M") if hasattr(timestamp, 'strftime') else str(timestamp)
        
        if params:
            params_str = "_".join([f"{k}={v}" for k, v in sorted(params.items())])
        else:
            params_str = "default"
        
        return f"{symbol}_{factor_id}_{timeframe}_{ts_str}_{params_str}"
    
    def calculate_batch(
        self,
        factor_ids: List[str],
        symbols: List[str],
        data_provider,  # 数据提供者，需要实现 get_data(symbol, timeframe) 方法
        timeframe: str = '1d',
        params: Optional[Dict[str, Dict[str, Any]]] = None,
        use_cache: bool = True
    ) -> Dict[str, Dict[str, pd.Series]]:
        """
        批量计算多个标的的因子
        
        Args:
            factor_ids: 因子ID列表
            symbols: 交易对列表
            data_provider: 数据提供者
            timeframe: 时间周期
            params: 因子参数
            use_cache: 是否使用缓存
            
        Returns:
            嵌套字典 {symbol: {factor_id: result_series}}
        """
        results = {}
        
        if self.parallel and len(symbols) > 1:
            # 并行计算
            with ProcessPoolExecutor(max_workers=self.max_workers) as executor:
                futures = {
                    executor.submit(
                        self._calculate_symbol,
                        symbol, factor_ids, data_provider, timeframe, params, use_cache
                    ): symbol
                    for symbol in symbols
                }
                
                for future in as_completed(futures):
                    symbol = futures[future]
                    try:
                        results[symbol] = future.result()
                    except Exception as e:
                        logger.error(f"Failed to calculate {symbol}: {e}")
                        results[symbol] = {}
        else:
            # 串行计算
            for symbol in symbols:
                try:
                    results[symbol] = self._calculate_symbol(
                        symbol, factor_ids, data_provider, timeframe, params, use_cache
                    )
                except Exception as e:
                    logger.error(f"Failed to calculate {symbol}: {e}")
                    results[symbol] = {}
        
        return results
    
    def _calculate_symbol(
        self,
        symbol: str,
        factor_ids: List[str],
        data_provider,
        timeframe: str,
        params: Optional[Dict[str, Dict[str, Any]]],
        use_cache: bool
    ) -> Dict[str, pd.Series]:
        """
        计算单个标的的因子（用于并行）
        
        Args:
            symbol: 交易对
            factor_ids: 因子ID列表
            data_provider: 数据提供者
            timeframe: 时间周期
            params: 因子参数
            use_cache: 是否使用缓存
            
        Returns:
            因子结果字典 {factor_id: result_series}
        """
        # 获取数据
        data = data_provider.get_data(symbol, timeframe)
        
        if data is None or len(data) == 0:
            logger.warning(f"No data for {symbol}")
            return {}
        
        # 计算因子
        return self.calculate(
            factor_ids=factor_ids,
            data=data,
            symbol=symbol,
            timeframe=timeframe,
            params=params,
            use_cache=use_cache
        )
    
    def clear_cache(self, pattern: Optional[str] = None) -> int:
        """
        清空缓存
        
        Args:
            pattern: 匹配模式
            
        Returns:
            清除的缓存条目数
        """
        if self.cache:
            return self.cache.clear(pattern)
        return 0
    
    def get_cache_stats(self) -> dict:
        """
        获取缓存统计信息
        
        Returns:
            统计信息字典
        """
        if self.cache:
            return self.cache.stats()
        return {}


# 便捷函数
def calculate_factors(
    factor_ids: List[str],
    data: pd.DataFrame,
    symbol: str,
    timeframe: str = '1d',
    params: Optional[Dict[str, Dict[str, Any]]] = None,
    use_cache: bool = True,
    cache: Optional[FactorCache] = None
) -> Dict[str, pd.Series]:
    """
    便捷函数：计算因子
    
    Args:
        factor_ids: 因子ID列表
        data: 市场数据
        symbol: 交易对
        timeframe: 时间周期
        params: 因子参数
        use_cache: 是否使用缓存
        cache: 缓存实例
        
    Returns:
        因子结果字典
    """
    calculator = FactorCalculator(cache=cache)
    return calculator.calculate(
        factor_ids=factor_ids,
        data=data,
        symbol=symbol,
        timeframe=timeframe,
        params=params,
        use_cache=use_cache
    )
