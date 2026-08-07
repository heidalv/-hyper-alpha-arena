"""
ATAS V2 - 订单簿因子

包含5个订单簿相关因子（模拟数据）
"""
import pandas as pd
import numpy as np
from ...factor_base import BaseFactor, FactorMetadata
from ...factor_registry import register_factor


@register_factor()
class BidAskSpreadFactor(BaseFactor):
    """买卖价差"""
    
    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id='bid_ask_spread',
            name='BidAskSpread',
            display_name='买卖价差',
            description='买卖价差的百分比',
            category='sentiment',
            subcategory='orderbook',
            lookback_period=1,
            required_data_fields=['high', 'low']
        )
    
    def calculate(self, data: pd.DataFrame) -> pd.Series:
        return (data['high'] - data['low']) / data['close']


@register_factor()
class OrderImbalanceFactor(BaseFactor):
    """订单不平衡度"""
    
    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id='order_imbalance',
            name='OrderImbalance',
            display_name='订单不平衡',
            description='买卖订单的不平衡程度',
            category='sentiment',
            subcategory='orderbook',
            lookback_period=1,
            required_data_fields=['volume', 'close']
        )
    
    def calculate(self, data: pd.DataFrame) -> pd.Series:
        price_change = data['close'].diff()
        buy_vol = data['volume'].where(price_change > 0, 0)
        sell_vol = data['volume'].where(price_change < 0, 0)
        return (buy_vol - sell_vol) / (buy_vol + sell_vol + 1e-10)


# 添加3个简化的订单簿因子
@register_factor()
class DepthRatioFactor(BaseFactor):
    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id='depth_ratio',
            name='DepthRatio',
            display_name='深度比率',
            description='市场深度指标',
            category='sentiment',
            subcategory='orderbook',
            lookback_period=1,
            required_data_fields=['volume']
        )
    def calculate(self, data: pd.DataFrame) -> pd.Series:
        return data['volume'] / data['volume'].rolling(20).mean()


@register_factor()
class PressureIndexFactor(BaseFactor):
    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id='pressure_index',
            name='PressureIndex',
            display_name='压力指数',
            description='买卖压力指标',
            category='sentiment',
            subcategory='orderbook',
            lookback_period=10,
            required_data_fields=['close', 'volume']
        )
    def calculate(self, data: pd.DataFrame) -> pd.Series:
        returns = data['close'].pct_change()
        return (returns * data['volume']).rolling(10).sum()


@register_factor()
class LiquidityScoreFactor(BaseFactor):
    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id='liquidity_score',
            name='LiquidityScore',
            display_name='流动性得分',
            description='综合流动性评分',
            category='sentiment',
            subcategory='orderbook',
            lookback_period=20,
            required_data_fields=['volume', 'close']
        )
    def calculate(self, data: pd.DataFrame) -> pd.Series:
        vol_score = data['volume'] / data['volume'].rolling(20).max()
        vol_ratio = data['close'].pct_change().rolling(20).std()
        return vol_score / (vol_ratio + 1e-10)
