"""
ATAS V2 - 宏观情绪因子

包含2个宏观因子:
- CryptoFearGreedFactor: 加密货币恐惧贪婪指数
- BTCDominanceFactor: BTC市值占比变化率

数据注入方式: unified_data_pool 在 K线 DataFrame 中注入
fear_greed, btc_dominance 列。
因子检查列是否存在，无数据时优雅降级为零/中性序列。

注意: factor_id 使用 'fear_greed_macro' 而非 'fear_greed_index'
以避免与 sentiment/market_sentiment_factors.py 中的已有因子冲突。
"""
import pandas as pd
import numpy as np
from typing import Dict, Any

from ...factor_base import BaseFactor, FactorMetadata
from ...factor_registry import register_factor


@register_factor()
class CryptoFearGreedFactor(BaseFactor):
    """
    加密货币恐惧贪婪指数
    数据源: alternative.me API（免费）
    标准化到 [-1, 1]: 50=中性, 0=极度恐惧(-1), 100=极度贪婪(+1)
    """

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id='fear_greed_macro',
            name='FearGreedMacro',
            display_name='恐惧贪婪指数(宏观)',
            description='加密市场恐惧贪婪指数(0=极度恐惧, 100=极度贪婪)，标准化到[-1,1]',
            category='macro',
            subcategory='sentiment',
            lookback_period=30,
            required_data_fields=['close'],
            cache_ttl=86400,
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        if 'fear_greed' in data.columns:
            fg = data['fear_greed'].fillna(50.0).astype(float)
            return (fg - 50) / 50
        return pd.Series(0.0, index=data.index, name='fear_greed_macro')


@register_factor()
class BTCDominanceFactor(BaseFactor):
    """
    BTC市值占比变化率因子
    BTC主导率上升 → 避险情绪（利空山寨币）
    BTC主导率下降 → 风险偏好（利多山寨币）
    数据源: CoinGecko API（免费）
    """

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id='btc_dominance',
            name='BTCDominance',
            display_name='BTC主导率',
            description='BTC市值占比变化率',
            category='macro',
            subcategory='market_structure',
            lookback_period=14,
            required_data_fields=['close'],
            cache_ttl=3600,
        )

    def get_default_params(self) -> Dict[str, Any]:
        return {'period': 7}

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        if 'btc_dominance' in data.columns:
            period = self.params.get('period', 7)
            return data['btc_dominance'].fillna(0.0).astype(float).pct_change(period)
        return pd.Series(0.0, index=data.index, name='btc_dominance')
