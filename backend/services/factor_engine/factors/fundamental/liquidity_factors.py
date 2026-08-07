"""
ATAS V2 - 流动性类基本面因子

包含5个流动性相关因子
"""
import pandas as pd
import numpy as np
from typing import Dict, Any
from ...factor_base import BaseFactor, VectorizedFactor, FactorMetadata
from ...factor_registry import register_factor


@register_factor()
class Volume1DFactor(VectorizedFactor):
    """1日成交量"""
    
    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id='volume_1d',
            name='Volume1D',
            display_name='1日成交量',
            description='当日成交量',
            category='fundamental',
            subcategory='liquidity',
            lookback_period=1,
            required_data_fields=['volume']
        )
    
    def calculate(self, data: pd.DataFrame) -> pd.Series:
        return data['volume']


@register_factor()
class VolumeChange7DFactor(VectorizedFactor):
    """7日成交量变化率"""
    
    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id='volume_change_7d',
            name='VolumeChange7D',
            display_name='7日成交量变化率',
            description='当日成交量相对7日均量的变化率',
            category='fundamental',
            subcategory='liquidity',
            lookback_period=7,
            required_data_fields=['volume']
        )
    
    def calculate(self, data: pd.DataFrame) -> pd.Series:
        ma7 = data['volume'].rolling(window=7).mean()
        return (data['volume'] - ma7) / ma7


@register_factor()
class AmihudIlliquidityFactor(BaseFactor):
    """Amihud非流动性指标"""
    
    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id='amihud_illiquidity',
            name='AmihudIlliquidity',
            display_name='Amihud非流动性',
            description='价格冲击成本，衡量市场深度',
            category='fundamental',
            subcategory='liquidity',
            lookback_period=20,
            required_data_fields=['close', 'volume']
        )
    
    def calculate(self, data: pd.DataFrame) -> pd.Series:
        returns = data['close'].pct_change().abs()
        dollar_volume = data['close'] * data['volume']
        illiq = returns / dollar_volume
        return illiq.rolling(window=20).mean()


@register_factor()
class TurnoverRatioFactor(BaseFactor):
    """换手率"""
    
    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id='turnover_ratio',
            name='TurnoverRatio',
            display_name='换手率',
            description='成交量与流通量的比率',
            category='fundamental',
            subcategory='liquidity',
            lookback_period=1,
            required_data_fields=['volume']
        )
    
    def calculate(self, data: pd.DataFrame) -> pd.Series:
        # 对于加密货币，使用成交量的滚动统计
        avg_vol_30d = data['volume'].rolling(window=30).mean()
        return data['volume'] / avg_vol_30d


@register_factor()
class DollarVolumeFactor(BaseFactor):
    """美元成交量"""
    
    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id='dollar_volume',
            name='DollarVolume',
            display_name='美元成交量',
            description='价格×成交量，衡量市场流动性',
            category='fundamental',
            subcategory='liquidity',
            lookback_period=1,
            required_data_fields=['close', 'volume']
        )
    
    def calculate(self, data: pd.DataFrame) -> pd.Series:
        return data['close'] * data['volume']
