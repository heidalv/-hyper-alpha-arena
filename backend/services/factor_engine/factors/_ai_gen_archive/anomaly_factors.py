"""
ATAS V2 - 异常检测因子

包含5个异常检测相关因子
"""
import pandas as pd
import numpy as np
from ...factor_base import BaseFactor, FactorMetadata
from ...factor_registry import register_factor


@register_factor()
class PriceAnomalyFactor(BaseFactor):
    """价格异常检测"""
    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id='price_anomaly',
            name='PriceAnomaly',
            display_name='价格异常',
            description='价格异常程度（Z-Score）',
            category='behavioral',
            subcategory='anomaly',
            lookback_period=20,
            required_data_fields=['close']
        )
    def calculate(self, data: pd.DataFrame) -> pd.Series:
        ma = data['close'].rolling(20).mean()
        std = data['close'].rolling(20).std()
        return (data['close'] - ma) / (std + 1e-10)


@register_factor()
class VolumeAnomalyFactor(BaseFactor):
    """成交量异常检测"""
    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id='volume_anomaly',
            name='VolumeAnomaly',
            display_name='成交量异常',
            description='成交量异常程度',
            category='behavioral',
            subcategory='anomaly',
            lookback_period=20,
            required_data_fields=['volume']
        )
    def calculate(self, data: pd.DataFrame) -> pd.Series:
        ma = data['volume'].rolling(20).mean()
        std = data['volume'].rolling(20).std()
        return (data['volume'] - ma) / (std + 1e-10)


@register_factor()
class VolatilityAnomalyFactor(BaseFactor):
    """波动率异常检测"""
    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id='volatility_anomaly',
            name='VolatilityAnomaly',
            display_name='波动率异常',
            description='波动率异常程度',
            category='behavioral',
            subcategory='anomaly',
            lookback_period=20,
            required_data_fields=['close']
        )
    def calculate(self, data: pd.DataFrame) -> pd.Series:
        returns_vol = data['close'].pct_change().rolling(20).std()
        vol_ma = returns_vol.rolling(60).mean()
        vol_std = returns_vol.rolling(60).std()
        return (returns_vol - vol_ma) / (vol_std + 1e-10)


@register_factor()
class SpikeDetectionFactor(BaseFactor):
    """价格尖峰检测"""
    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id='spike_detection',
            name='SpikeDetection',
            display_name='价格尖峰',
            description='检测价格突然的剧烈变化',
            category='behavioral',
            subcategory='anomaly',
            lookback_period=5,
            required_data_fields=['close']
        )
    def calculate(self, data: pd.DataFrame) -> pd.Series:
        returns = data['close'].pct_change()
        threshold = returns.rolling(20).std() * 3
        return (returns.abs() > threshold).astype(int)


@register_factor()
class RegimeChangeFactor(BaseFactor):
    """市场状态变化检测"""
    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id='regime_change',
            name='RegimeChange',
            display_name='状态变化',
            description='检测市场状态转换',
            category='behavioral',
            subcategory='anomaly',
            lookback_period=30,
            required_data_fields=['close']
        )
    def calculate(self, data: pd.DataFrame) -> pd.Series:
        short_vol = data['close'].pct_change().rolling(10).std()
        long_vol = data['close'].pct_change().rolling(30).std()
        return (short_vol - long_vol) / (long_vol + 1e-10)
