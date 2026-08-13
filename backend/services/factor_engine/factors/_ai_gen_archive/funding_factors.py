"""
ATAS V2 - 资金费率因子

包含5个资金费率相关因子（加密货币市场）

v3.0 升级: 修复 P0 级 Bug —— 所有因子改为读取真实 funding_rate 数据
而非使用 price pct_change 进行错误估算。

数据注入方式: unified_data_pool.capture_snapshot() 在 K线 DataFrame 中
注入 'funding_rate' 列。因子直接从 data['funding_rate'] 读取真实数据。
当 funding_rate 列不存在时优雅降级为零序列。
"""
import pandas as pd
import numpy as np
from ...factor_base import BaseFactor, FactorMetadata
from ...factor_registry import register_factor


@register_factor()
class FundingRateSimpleFactor(BaseFactor):
    """真实资金费率因子"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id='funding_rate',
            name='FundingRate',
            display_name='资金费率',
            description='交易所真实资金费率数据',
            category='sentiment',
            subcategory='funding',
            lookback_period=1,
            required_data_fields=['close', 'funding_rate']
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        if 'funding_rate' in data.columns:
            return data['funding_rate'].fillna(0.0)
        # 降级：无 funding_rate 列时返回零序列
        return pd.Series(0.0, index=data.index, name='funding_rate')


@register_factor()
class FundingRate24hFactor(BaseFactor):
    """24小时平均资金费率因子"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id='funding_rate_24h',
            name='FundingRate24h',
            display_name='24h资金费率',
            description='24小时滚动平均资金费率',
            category='sentiment',
            subcategory='funding',
            lookback_period=24,
            required_data_fields=['close', 'funding_rate']
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        if 'funding_rate' in data.columns:
            return data['funding_rate'].fillna(0.0).rolling(24).mean()
        return pd.Series(0.0, index=data.index, name='funding_rate_24h')


@register_factor()
class FundingRateMaFactor(BaseFactor):
    """资金费率移动平均因子"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id='funding_rate_ma',
            name='FundingRateMA',
            display_name='资金费率均线',
            description='资金费率移动平均',
            category='sentiment',
            subcategory='funding',
            lookback_period=20,
            required_data_fields=['close', 'funding_rate']
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        if 'funding_rate' in data.columns:
            return data['funding_rate'].fillna(0.0).rolling(20).mean()
        return pd.Series(0.0, index=data.index, name='funding_rate_ma')


@register_factor()
class FundingRateVolFactor(BaseFactor):
    """资金费率波动率因子"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id='funding_rate_vol',
            name='FundingRateVol',
            display_name='资金费率波动',
            description='资金费率波动率',
            category='sentiment',
            subcategory='funding',
            lookback_period=20,
            required_data_fields=['close', 'funding_rate']
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        if 'funding_rate' in data.columns:
            return data['funding_rate'].fillna(0.0).rolling(20).std()
        return pd.Series(0.0, index=data.index, name='funding_rate_vol')


@register_factor()
class FundingRateExtremeFactor(BaseFactor):
    """极端资金费率因子（Z-Score）"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id='funding_rate_extreme',
            name='FundingRateExtreme',
            display_name='极端资金费率',
            description='资金费率极值指标（Z-Score）',
            category='sentiment',
            subcategory='funding',
            lookback_period=20,
            required_data_fields=['close', 'funding_rate']
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        if 'funding_rate' in data.columns:
            fr = data['funding_rate'].fillna(0.0)
            return (fr - fr.rolling(20).mean()) / (fr.rolling(20).std() + 1e-10)
        return pd.Series(0.0, index=data.index, name='funding_rate_extreme')
