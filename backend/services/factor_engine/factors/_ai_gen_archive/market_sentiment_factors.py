"""
ATAS V2 - 市场情绪因子

包含5个市场情绪相关因子
"""
import pandas as pd
import numpy as np
from ...factor_base import BaseFactor, VectorizedFactor, FactorMetadata
from ...factor_registry import register_factor


@register_factor()
class BullBearIndexFactor(BaseFactor):
    """多空指数"""
    
    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id='bull_bear_index',
            name='BullBearIndex',
            display_name='多空指数',
            description='基于价格动量的多空情绪指标',
            category='sentiment',
            subcategory='market',
            lookback_period=20,
            required_data_fields=['close']
        )
    
    def calculate(self, data: pd.DataFrame) -> pd.Series:
        returns = data['close'].pct_change()
        bull_power = returns.where(returns > 0, 0).rolling(20).sum()
        bear_power = -returns.where(returns < 0, 0).rolling(20).sum()
        return (bull_power - bear_power) / (bull_power + bear_power + 1e-10)


@register_factor()
class FearGreedIndexFactor(BaseFactor):
    """恐慌贪婪指数"""
    
    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id='fear_greed_index',
            name='FearGreed',
            display_name='恐慌贪婪指数',
            description='基于波动率和成交量的情绪指标',
            category='sentiment',
            subcategory='market',
            lookback_period=30,
            required_data_fields=['close', 'volume']
        )
    
    def calculate(self, data: pd.DataFrame) -> pd.Series:
        vol = data['close'].pct_change().rolling(30).std()
        vol_change = vol / vol.rolling(90).mean()
        volume_change = data['volume'] / data['volume'].rolling(30).mean()
        return (vol_change + volume_change) / 2


@register_factor()
class MarketMomentumFactor(VectorizedFactor):
    """市场动量指数"""
    
    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id='market_momentum',
            name='MarketMomentum',
            display_name='市场动量',
            description='短期动量指标',
            category='sentiment',
            subcategory='market',
            lookback_period=10,
            required_data_fields=['close']
        )
    
    def calculate(self, data: pd.DataFrame) -> pd.Series:
        return data['close'].pct_change(10)


@register_factor()
class VolatilityRatioFactor(BaseFactor):
    """波动率比率"""
    
    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id='volatility_ratio',
            name='VolatilityRatio',
            display_name='波动率比率',
            description='短期波动率与长期波动率的比率',
            category='sentiment',
            subcategory='market',
            lookback_period=20,
            required_data_fields=['close']
        )
    
    def calculate(self, data: pd.DataFrame) -> pd.Series:
        short_vol = data['close'].pct_change().rolling(10).std()
        long_vol = data['close'].pct_change().rolling(30).std()
        return short_vol / (long_vol + 1e-10)


@register_factor()
class TrendStrengthFactor(BaseFactor):
    """趋势强度因子"""
    
    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id='trend_strength',
            name='TrendStrength',
            display_name='趋势强度',
            description='衡量当前趋势的强度',
            category='sentiment',
            subcategory='market',
            lookback_period=20,
            required_data_fields=['close']
        )
    
    def calculate(self, data: pd.DataFrame) -> pd.Series:
        ma20 = data['close'].rolling(20).mean()
        return (data['close'] - ma20) / ma20
