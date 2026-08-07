"""
ATAS V2 - 流式因子计算器

支持增量计算和实时数据流处理
"""
import pandas as pd
import numpy as np
from typing import Dict, List, Optional, Any
from datetime import datetime
import asyncio
from concurrent.futures import ThreadPoolExecutor

from .factor_base import BaseFactor
from .factor_registry import FactorRegistry
from .factor_cache import FactorCache


class StreamFactorCalculator:
    """流式因子计算器 - 支持增量更新"""
    
    def __init__(
        self,
        registry: Optional[FactorRegistry] = None,
        cache: Optional[FactorCache] = None,
        max_workers: int = 4
    ):
        self.registry = registry or FactorRegistry()
        self.cache = cache
        self.executor = ThreadPoolExecutor(max_workers=max_workers)
        
        # 状态缓存：存储每个因子的历史状态
        self.state_cache: Dict[str, Dict] = {}
    
    async def calculate_incremental(
        self,
        factor_ids: List[str],
        new_data: pd.DataFrame,
        symbol: str,
        timeframe: str = '1d',
        params: Optional[Dict] = None
    ) -> Dict[str, pd.Series]:
        """
        增量计算因子 - 只计算新增数据点
        
        Args:
            factor_ids: 因子ID列表
            new_data: 新增的数据（可以是1行或多行）
            symbol: 交易对
            timeframe: 时间周期
            params: 参数覆盖
            
        Returns:
            因子计算结果字典
        """
        # 解析依赖关系
        ordered_ids = self.registry.resolve_dependencies(factor_ids)
        
        results = {}
        
        for factor_id in ordered_ids:
            try:
                # 获取因子状态
                state_key = f"{symbol}_{timeframe}_{factor_id}"
                prev_state = self.state_cache.get(state_key, {})
                
                # 获取因子实例
                factor = self.registry.get(factor_id, params)
                metadata = factor.get_metadata()
                
                # 检查是否支持增量计算
                if hasattr(factor, 'calculate_incremental'):
                    # 增量计算
                    result = factor.calculate_incremental(
                        new_data=new_data,
                        prev_state=prev_state
                    )
                else:
                    # 需要完整数据的因子，获取历史数据
                    lookback = metadata.lookback_period
                    full_data = self._get_full_data(symbol, timeframe, lookback, new_data)
                    result = factor.calculate(full_data)
                    result = result.iloc[-len(new_data):]  # 只返回新数据部分
                
                results[factor_id] = result
                
                # 更新状态
                if hasattr(factor, 'get_state'):
                    new_state = factor.get_state()
                    self.state_cache[state_key] = new_state
                    
            except Exception as e:
                print(f"Error calculating factor {factor_id}: {str(e)}")
                results[factor_id] = pd.Series([np.nan] * len(new_data))
        
        return {fid: results[fid] for fid in factor_ids}
    
    async def calculate_stream(
        self,
        factor_ids: List[str],
        data_stream: asyncio.Queue,
        symbol: str,
        timeframe: str = '1d',
        params: Optional[Dict] = None,
        callback: Optional[callable] = None
    ):
        """
        流式计算因子 - 从数据流中持续计算
        
        Args:
            factor_ids: 因子ID列表
            data_stream: 异步数据流队列
            symbol: 交易对
            timeframe: 时间周期
            params: 参数覆盖
            callback: 结果回调函数
        """
        print(f"Starting stream calculation for {len(factor_ids)} factors...")
        
        while True:
            try:
                # 从队列获取新数据
                new_data_point = await asyncio.wait_for(
                    data_stream.get(),
                    timeout=30.0
                )
                
                if new_data_point is None:  # 终止信号
                    break
                
                # 转换为DataFrame
                if isinstance(new_data_point, dict):
                    new_data = pd.DataFrame([new_data_point])
                else:
                    new_data = new_data_point
                
                # 增量计算
                results = await self.calculate_incremental(
                    factor_ids=factor_ids,
                    new_data=new_data,
                    symbol=symbol,
                    timeframe=timeframe,
                    params=params
                )
                
                # 回调
                if callback:
                    await callback(results, new_data)
                    
            except asyncio.TimeoutError:
                print("Stream timeout, waiting for more data...")
            except Exception as e:
                print(f"Stream calculation error: {str(e)}")
    
    def calculate_batch_parallel(
        self,
        factor_ids: List[str],
        symbols: List[str],
        data_dict: Dict[str, pd.DataFrame],
        timeframe: str = '1d',
        params: Optional[Dict] = None
    ) -> Dict[str, Dict[str, pd.Series]]:
        """
        并行批量计算 - 多币对同时计算
        
        Args:
            factor_ids: 因子ID列表
            symbols: 交易对列表
            data_dict: 各交易对的数据 {symbol: DataFrame}
            timeframe: 时间周期
            params: 参数覆盖
            
        Returns:
            嵌套字典 {symbol: {factor_id: Series}}
        """
        from concurrent.futures import as_completed
        
        futures = {}
        
        # 提交所有计算任务
        for symbol in symbols:
            if symbol not in data_dict:
                continue
            
            future = self.executor.submit(
                self._calculate_symbol,
                factor_ids,
                data_dict[symbol],
                symbol,
                timeframe,
                params
            )
            futures[future] = symbol
        
        # 收集结果
        results = {}
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                results[symbol] = future.result()
            except Exception as e:
                print(f"Error calculating {symbol}: {str(e)}")
                results[symbol] = {}
        
        return results
    
    def _calculate_symbol(
        self,
        factor_ids: List[str],
        data: pd.DataFrame,
        symbol: str,
        timeframe: str,
        params: Optional[Dict]
    ) -> Dict[str, pd.Series]:
        """单个交易对的因子计算（用于并行）"""
        ordered_ids = self.registry.resolve_dependencies(factor_ids)
        
        results = {}
        for factor_id in ordered_ids:
            try:
                factor = self.registry.get(factor_id, params)
                results[factor_id] = factor.calculate(data)
            except Exception as e:
                print(f"Error in {symbol}/{factor_id}: {str(e)}")
                results[factor_id] = pd.Series([np.nan] * len(data))
        
        return {fid: results[fid] for fid in factor_ids}
    
    def _get_full_data(
        self,
        symbol: str,
        timeframe: str,
        lookback: int,
        new_data: pd.DataFrame
    ) -> pd.DataFrame:
        """
        获取完整历史数据（用于不支持增量的因子）
        
        实际应用中应该从数据库或缓存中获取历史数据
        这里简化处理，仅返回new_data
        """
        # TODO: 从数据库/缓存获取历史数据
        # 目前简化实现
        return new_data
    
    def clear_state(self, symbol: Optional[str] = None):
        """清理状态缓存"""
        if symbol:
            keys_to_remove = [k for k in self.state_cache.keys() if k.startswith(symbol)]
            for key in keys_to_remove:
                del self.state_cache[key]
        else:
            self.state_cache.clear()
    
    def shutdown(self):
        """关闭计算器"""
        self.executor.shutdown(wait=True)
