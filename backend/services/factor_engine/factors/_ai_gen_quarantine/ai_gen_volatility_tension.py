"""AI因子: 波动率紧张反转因子 | 置信:60% | 捕捉波动率快速扩张后价格处于区间边界的反转时机。使用ATR变化率衡量波动加速度，结合价格在布林带上轨/下轨的位置。当波动率突然放大且价格触及上轨时认为多头过度拥挤，因子值为负（看跌反转）；触及下轨且波动率放大时因子值为正（看涨反转）。震荡市中接近0。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volatility_Tension_Reversal_Indicator(BaseFactor):
    """捕捉波动率快速扩张后价格处于区间边界的反转时机。使用ATR变化率衡量波动加速度，结合价格在布林带上轨/下轨的位置。当波动率突然放大且价格触及上轨时认为多头过度拥挤，因子值为负（看跌反转）；触及下轨且波动率放大时因子值为正（看涨反转）。震荡市中接近0。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volatility_tension",
            name="Volatility Tension Reversal Indicator",
            display_name="波动率紧张反转因子",
            description="捕捉波动率快速扩张后价格处于区间边界的反转时机。使用ATR变化率衡量波动加速度，结合价格在布林带上轨/下轨的位置。当波动率突然放大且价格触及上轨时认为多头过度拥挤，因子值为负（看跌反转）；触及下轨且波动率放大时因子值为正（看涨反转）。震荡市中接近0。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        close = data['close']
        high = data['high']
        low = data['low']

        # 计算ATR（14日）
        tr1 = high - low
        tr2 = abs(high - close.shift(1))
        tr3 = abs(low - close.shift(1))
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.rolling(14).mean()

        # ATR变化率（加速度）
        atr_change = atr.pct_change(3)  # 3期变化率

        # 布林带（20日，2倍标准差）
        ma20 = close.rolling(20).mean()
        std20 = close.rolling(20).std()
        upper = ma20 + 2 * std20
        lower = ma20 - 2 * std20

        # 价格在布林带内的位置，-1在下轨，+1在上轨
        band_position = (close - ma20) / (2 * std20 + 1e-10)
        band_position = band_position.clip(-1, 1)

        # 波动率张力：波动率扩张时atr_change为正，此时价格在极端位置则反转信号强
        # 扩张+上轨 => 负值 (看跌)；扩张+下轨 => 正值 (看涨)
        tension = -np.sign(band_position) * atr_change
        # 当波动率收缩或无变化时信号弱化
        tension = np.where(atr_change.abs() < 0.02, 0, tension)
        # 归一化到[-1,1]
        result = np.tanh(tension * 10)
        return pd.Series(result, index=close.index)
