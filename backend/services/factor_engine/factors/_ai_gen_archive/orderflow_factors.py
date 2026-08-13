"""
ATAS V2 - 订单流因子

包含5个订单流相关因子
"""
import pandas as pd
import numpy as np
from ...factor_base import BaseFactor, FactorMetadata
from ...factor_registry import register_factor


@register_factor()
class AggressiveBuyFactor(BaseFactor):
    """激进买入"""
    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id='aggressive_buy',
            name='AggressiveBuy',
            display_name='激进买入',
            description='激进买入成交量估算',
            category='behavioral',
            subcategory='orderflow',
            lookback_period=1,
            required_data_fields=['close', 'volume', 'high']
        )
    def calculate(self, data: pd.DataFrame) -> pd.Series:
        price_strength = (data['close'] - data['low']) / (data['high'] - data['low'] + 1e-10)
        return data['volume'] * price_strength


@register_factor()
class AggressiveSellFactor(BaseFactor):
    """激进卖出"""
    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id='aggressive_sell',
            name='AggressiveSell',
            display_name='激进卖出',
            description='激进卖出成交量估算',
            category='behavioral',
            subcategory='orderflow',
            lookback_period=1,
            required_data_fields=['close', 'volume', 'high', 'low']
        )
    def calculate(self, data: pd.DataFrame) -> pd.Series:
        price_weakness = (data['high'] - data['close']) / (data['high'] - data['low'] + 1e-10)
        return data['volume'] * price_weakness


@register_factor()
class OrderFlowDeltaFactor(BaseFactor):
    """订单流Delta"""
    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id='orderflow_delta',
            name='OrderFlowDelta',
            display_name='订单流Delta',
            description='买卖订单流差值',
            category='behavioral',
            subcategory='orderflow',
            lookback_period=20,
            required_data_fields=['close', 'volume']
        )
    def calculate(self, data: pd.DataFrame) -> pd.Series:
        price_change = data['close'].diff()
        buy_vol = data['volume'].where(price_change > 0, 0)
        sell_vol = data['volume'].where(price_change < 0, 0)
        delta = (buy_vol - sell_vol).rolling(20).sum()
        return delta / data['volume'].rolling(20).sum()


@register_factor()
class TradeIntensityFactor(BaseFactor):
    """交易强度"""
    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id='trade_intensity',
            name='TradeIntensity',
            display_name='交易强度',
            description='成交强度指标',
            category='behavioral',
            subcategory='orderflow',
            lookback_period=10,
            required_data_fields=['volume', 'close']
        )
    def calculate(self, data: pd.DataFrame) -> pd.Series:
        price_move = data['close'].pct_change().abs()
        return data['volume'] * price_move


@register_factor()
class FlowRatioFactor(BaseFactor):
    """流量比率"""
    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id='flow_ratio',
            name='FlowRatio',
            display_name='流量比率',
            description='订单流量比率',
            category='behavioral',
            subcategory='orderflow',
            lookback_period=20,
            required_data_fields=['volume']
        )
    def calculate(self, data: pd.DataFrame) -> pd.Series:
        short_vol = data['volume'].rolling(5).mean()
        long_vol = data['volume'].rolling(20).mean()
        return short_vol / (long_vol + 1e-10)
