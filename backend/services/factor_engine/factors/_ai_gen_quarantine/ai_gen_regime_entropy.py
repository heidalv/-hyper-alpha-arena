"""AI因子: 状态熵 | 置信:60% | 基于价格在最近N个周期内高低区间中的相对位置变化频繁程度，反映市场是否处于无序震荡。计算连续两个收盘价变化方向翻转的频率，以及价格波动幅度与历史均值的比值。输出越高（接近+1）表示高度无序（应避免交易），越低（接近-1）表示有明确方向。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Regime_Entropy(BaseFactor):
    """基于价格在最近N个周期内高低区间中的相对位置变化频繁程度，反映市场是否处于无序震荡。计算连续两个收盘价变化方向翻转的频率，以及价格波动幅度与历史均值的比值。输出越高（接近+1）表示高度无序（应避免交易），越低（接近-1）表示有明确方向。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_regime_entropy",
            name="Regime Entropy",
            display_name="状态熵",
            description="基于价格在最近N个周期内高低区间中的相对位置变化频繁程度，反映市场是否处于无序震荡。计算连续两个收盘价变化方向翻转的频率，以及价格波动幅度与历史均值的比值。输出越高（接近+1）表示高度无序（应避免交易），越低（接近-1）表示有明确方向。",
            category="behavioral",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        N = 20
        direction = np.sign(data['close'].diff())
        flip_count = (direction.diff() != 0).rolling(N).sum()
        max_flip = N - 1
        flip_ratio = flip_count / max_flip
        amp = (data['high'] - data['low']) / data['close']
        amp_mean = amp.rolling(N).mean()
        amp_norm = amp / amp_mean.replace(0, np.nan) - 1
        amp_norm = amp_norm.clip(-1, 1)
        result = (flip_ratio * 2 - 1) * 0.5 + amp_norm * 0.5
        return result.fillna(0)
