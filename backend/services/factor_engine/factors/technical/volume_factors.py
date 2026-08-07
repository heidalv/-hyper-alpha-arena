"""
ATAS V2 - 成交量类技术指标因子

包含2个成交量类因子
"""
import pandas as pd
import numpy as np
from typing import Dict, Any
from ...factor_base import BaseFactor, VectorizedFactor, FactorMetadata
from ...factor_registry import register_factor


@register_factor()
class ChaikinMoneyFlowFactor(BaseFactor):
    """蔡金资金流量指标"""
    
    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id='cmf_20',
            name='CMF20',
            display_name='20日蔡金资金流',
            description='衡量资金流向强度，正值表示资金流入',
            category='technical',
            subcategory='volume',
            lookback_period=20,
            required_data_fields=['high', 'low', 'close', 'volume']
        )
    
    def calculate(self, data: pd.DataFrame) -> pd.Series:
        period = self.params.get('period', 20)
        
        # 资金流量乘数
        mfm = ((data['close'] - data['low']) - (data['high'] - data['close'])) / (data['high'] - data['low'])
        mfm = mfm.fillna(0)
        
        # 资金流量
        mfv = mfm * data['volume']
        
        # 蔡金资金流量
        cmf = mfv.rolling(window=period).sum() / data['volume'].rolling(window=period).sum()
        return cmf


@register_factor()
class ForceIndexFactor(VectorizedFactor):
    """强力指数"""
    
    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id='force_index',
            name='ForceIndex',
            display_name='强力指数',
            description='价格变化和成交量的乘积，衡量买卖力度',
            category='technical',
            subcategory='volume',
            lookback_period=13,
            required_data_fields=['close', 'volume']
        )
    
    def calculate(self, data: pd.DataFrame) -> pd.Series:
        period = self.params.get('period', 13)
        
        price_change = data['close'].diff()
        force = price_change * data['volume']
        force_index = force.ewm(span=period, adjust=False).mean()
        return force_index
