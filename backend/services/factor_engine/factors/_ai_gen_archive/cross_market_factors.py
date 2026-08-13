"""
ATAS V2 - 跨市场关联因子

包含3个跨市场因子:
- CrossMarketCorrelationFactor: BTC 与 SPX/DXY 的相关性
- RiskOnScoreFactor: 宏观风险偏好评分
- GlobalLiquidityFactor: 全球流动性代理指标

数据注入方式: unified_data_pool 在 K线 DataFrame 中注入
risk_on_score, cross_market_corr 列。
因子检查列是否存在，无数据时优雅降级为零/中性序列。
"""
import pandas as pd
import numpy as np
from typing import Dict, Any

from ...factor_base import BaseFactor, FactorMetadata
from ...factor_registry import register_factor


@register_factor()
class CrossMarketCorrelationFactor(BaseFactor):
    """
    跨市场相关性因子
    BTC 与 SPX/DXY 的 30 日相关系数
    值域: [-1, 1]
    正值 = BTC 与美股正相关 (risk_on 环境)
    负值 = BTC 与美元正相关 (通常利空)
    """

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id='cross_market_corr',
            name='CrossMarketCorrelation',
            display_name='跨市场相关性',
            description='BTC与SPX/DXY的30日相关系数，衡量加密市场与传统金融的关联度',
            category='macro',
            subcategory='cross_market',
            lookback_period=30,
            required_data_fields=['close'],
            cache_ttl=14400,  # 4小时缓存
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        if 'cross_market_corr' in data.columns:
            return data['cross_market_corr'].fillna(0.0).astype(float)
        return pd.Series(0.0, index=data.index, name='cross_market_corr')


@register_factor()
class RiskOnScoreFactor(BaseFactor):
    """
    宏观风险偏好评分因子
    综合考虑 DXY/SPX/恐贪指数等宏观指标
    值域: [-1, 1]
    正值 = 风险偏好 (risk_on)，利好加密市场
    负值 = 风险规避 (risk_off)，利空加密市场
    """

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id='risk_on_score',
            name='RiskOnScore',
            display_name='风险偏好评分',
            description='基于DXY/SPX/恐贪指数的宏观风险偏好综合评分(-1~+1)',
            category='macro',
            subcategory='risk_sentiment',
            lookback_period=5,
            required_data_fields=['close'],
            cache_ttl=14400,
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        if 'risk_on_score' in data.columns:
            return data['risk_on_score'].fillna(0.0).astype(float)
        return pd.Series(0.0, index=data.index, name='risk_on_score')


@register_factor()
class GlobalLiquidityFactor(BaseFactor):
    """
    全球流动性代理指标
    使用联邦基金利率的反向指标作为流动性代理
    低利率 → 高流动性 → 利好加密 (正值)
    高利率 → 低流动性 → 利空加密 (负值)
    """

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id='global_liquidity',
            name='GlobalLiquidity',
            display_name='全球流动性',
            description='基于联邦基金利率的全球流动性代理指标',
            category='macro',
            subcategory='liquidity',
            lookback_period=30,
            required_data_fields=['close'],
            cache_ttl=86400,  # 24小时缓存
        )

    def get_default_params(self) -> Dict[str, Any]:
        return {'neutral_rate': 3.0, 'sensitivity': 0.2}

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        if 'global_liquidity' in data.columns:
            return data['global_liquidity'].fillna(0.0).astype(float)

        # 如果有 fed_funds_rate 列，计算流动性指标
        if 'fed_funds_rate' in data.columns:
            rate = data['fed_funds_rate'].fillna(3.0).astype(float)
            neutral = self.params.get('neutral_rate', 3.0)
            sensitivity = self.params.get('sensitivity', 0.2)
            # 利率低于中性 → 正值（流动性好），利率高于中性 → 负值
            return np.clip((neutral - rate) * sensitivity, -1, 1)

        return pd.Series(0.0, index=data.index, name='global_liquidity')
