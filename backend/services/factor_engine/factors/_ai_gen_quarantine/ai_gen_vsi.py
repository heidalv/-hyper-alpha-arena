"""AI因子: 量价分歧信号 | 置信:60% | 计算成交量相对过去N周期的倍数与价格变动幅度相对过去N周期变动幅度的比值，当成交量显著放大但价格变动极小时，表明多空博弈激烈但无方向，容易发生止损，输出负值。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumeSpreadDivergence(BaseFactor):
    """计算成交量相对过去N周期的倍数与价格变动幅度相对过去N周期变动幅度的比值，当成交量显著放大但价格变动极小时，表明多空博弈激烈但无方向，容易发生止损，输出负值。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vsi",
            name="Volume-Spread Divergence",
            display_name="量价分歧信号",
            description="计算成交量相对过去N周期的倍数与价格变动幅度相对过去N周期变动幅度的比值，当成交量显著放大但价格变动极小时，表明多空博弈激烈但无方向，容易发生止损，输出负值。",
            category="behavioral",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        n = 20
        volume = data['volume']
        close = data['close']
        vol_ma = volume.rolling(n).mean()
        vol_ratio = volume / vol_ma
        price_change = close.pct_change().abs()
        price_ma = price_change.rolling(n).mean()
        price_ratio = price_change / price_ma.replace(0, 1e-10)
        # 成交量放大3倍以上但价格变动仅为均值的一半以下
        cond = (vol_ratio > 3.0) & (price_ratio < 0.5)
        result = -cond.astype(float) * 1.0
        result.fillna(0.0, inplace=True)
        return result
