"""
ATAS V2 - 期权结构因子

包含2个期权结构相关因子:
- OptionsStructureFactor: 期权偏斜与隐含波动率结构分析
- OpenInterestFactor: 独立OI变化因子（与funding解耦）

数据注入方式: unified_data_pool 在 K线 DataFrame 中注入
options_skew, iv_term_structure, oi 等列。
因子检查列是否存在，无数据时优雅降级。
"""
import pandas as pd
import numpy as np
from typing import Dict, Any

from ...factor_base import BaseFactor, FactorMetadata
from ...factor_registry import register_factor


@register_factor()
class OptionsStructureFactor(BaseFactor):
    """
    期权偏斜因子

    看跌/看涨IV比率 > 1 → 市场恐慌（下行保护需求强）
    看跌/看涨IV比率 < 1 → 市场贪婪（上行投机活跃）
    结合期限结构（近月/远月IV之比）判断市场焦虑程度

    数据源: Deribit / 聚合平台
    """

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id='options_structure',
            name='OptionsStructure',
            display_name='期权偏斜结构',
            description='看跌/看涨隐含波动率偏斜及期限结构',
            category='derivatives',
            subcategory='options',
            lookback_period=24,
            required_data_fields=['close'],
            cache_ttl=3600,
        )

    def get_default_params(self) -> Dict[str, Any]:
        return {'window': 24, 'skew_threshold': 0.1}

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        window = self.params.get('window', 24)

        has_skew = 'options_skew' in data.columns
        has_iv = 'iv_term_structure' in data.columns

        if not has_skew and not has_iv:
            return pd.Series(0.0, index=data.index, name='options_structure')

        score = pd.Series(0.0, index=data.index)

        if has_skew:
            skew = data['options_skew'].fillna(0.0)
            skew_mean = skew.rolling(window).mean()
            skew_std = skew.rolling(window).std()
            skew_z = (skew - skew_mean) / (skew_std + 1e-10)
            score = score + skew_z * 0.6

        if has_iv:
            iv_ts = data['iv_term_structure'].fillna(1.0)
            iv_deviation = iv_ts - 1.0
            iv_mean = iv_deviation.rolling(window).mean()
            iv_std = iv_deviation.rolling(window).std()
            iv_z = (iv_deviation - iv_mean) / (iv_std + 1e-10)
            score = score + iv_z * 0.4

        return score


@register_factor()
class OpenInterestFactor(BaseFactor):
    """
    独立持仓量(OI)变化因子

    OI快速增长 → 新资金入场，趋势可能加速
    OI快速下降 → 头寸平仓，趋势可能减弱
    OI变化与价格方向的一致性分析

    与 FundingOIDivergenceFactor 不同，此因子独立分析OI变化，
    不依赖 funding_rate 数据。
    """

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id='open_interest_momentum',
            name='OpenInterestMomentum',
            display_name='持仓量动量',
            description='持仓量变化率及与价格的一致性分析',
            category='derivatives',
            subcategory='positioning',
            lookback_period=24,
            required_data_fields=['close'],
            cache_ttl=3600,
        )

    def get_default_params(self) -> Dict[str, Any]:
        return {'window': 24, 'fast_window': 6}

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        if 'oi' not in data.columns:
            return pd.Series(0.0, index=data.index, name='open_interest_momentum')

        window = self.params.get('window', 24)
        fast_window = self.params.get('fast_window', 6)

        # v3 整改: pandas >= 2.2 起 fillna(method=...) 已废弃，改用 .ffill()
        oi = data['oi'].ffill().fillna(0).astype(float)
        close = data['close'].astype(float)

        oi_change = oi.pct_change(fast_window).fillna(0)
        price_change = close.pct_change(fast_window).fillna(0)

        oi_mean = oi_change.rolling(window).mean()
        oi_std = oi_change.rolling(window).std()
        oi_z = (oi_change - oi_mean) / (oi_std + 1e-10)

        # OI和价格同向变动 → 信号更强
        alignment = np.sign(oi_change) * np.sign(price_change)
        aligned_score = oi_z * (1 + 0.3 * alignment)

        return aligned_score
