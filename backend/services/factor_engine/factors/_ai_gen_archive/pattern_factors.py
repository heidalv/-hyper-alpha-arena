"""
ATAS V2 - 交易模式因子

包含5个交易模式相关因子
"""
import pandas as pd
import numpy as np
from ...factor_base import BaseFactor, FactorMetadata
from ...factor_registry import register_factor


@register_factor()
class WinStreakFactor(BaseFactor):
    """连涨天数"""
    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id='win_streak',
            name='WinStreak',
            display_name='连涨天数',
            description='连续上涨的天数',
            category='behavioral',
            subcategory='pattern',
            lookback_period=20,
            required_data_fields=['close']
        )
    def calculate(self, data: pd.DataFrame) -> pd.Series:
        returns = data['close'].diff() > 0
        streak = returns.groupby((returns != returns.shift()).cumsum()).cumsum()
        return streak * returns


@register_factor()
class PriceRangeFactor(BaseFactor):
    """价格震荡幅度"""
    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id='price_range',
            name='PriceRange',
            display_name='价格震荡幅度',
            description='一定周期内的价格震荡幅度',
            category='behavioral',
            subcategory='pattern',
            lookback_period=20,
            required_data_fields=['high', 'low']
        )
    def calculate(self, data: pd.DataFrame) -> pd.Series:
        high_max = data['high'].rolling(20).max()
        low_min = data['low'].rolling(20).min()
        return (high_max - low_min) / data['close']


@register_factor()
class GapSizeFactor(BaseFactor):
    """跳空缺口"""
    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id='gap_size',
            name='GapSize',
            display_name='跳空缺口',
            description='开盘价与前收盘价的差距',
            category='behavioral',
            subcategory='pattern',
            lookback_period=1,
            required_data_fields=['open', 'close']
        )
    def calculate(self, data: pd.DataFrame) -> pd.Series:
        prev_close = data['close'].shift(1)
        return (data['open'] - prev_close) / prev_close


@register_factor()
class ReversePatternFactor(BaseFactor):
    """反转模式"""
    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id='reverse_pattern',
            name='ReversePattern',
            display_name='反转模式',
            description='价格反转模式强度',
            category='behavioral',
            subcategory='pattern',
            lookback_period=5,
            required_data_fields=['close']
        )
    def calculate(self, data: pd.DataFrame) -> pd.Series:
        returns = data['close'].pct_change()
        return -returns.rolling(5).mean() * returns


@register_factor()
class ConsolidationFactor(BaseFactor):
    """盘整模式"""
    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id='consolidation',
            name='Consolidation',
            display_name='盘整模式',
            description='价格盘整程度',
            category='behavioral',
            subcategory='pattern',
            lookback_period=20,
            required_data_fields=['high', 'low', 'close']
        )
    def calculate(self, data: pd.DataFrame) -> pd.Series:
        range_pct = (data['high'] - data['low']) / data['close']
        return 1 / (range_pct.rolling(20).mean() + 1e-10)
