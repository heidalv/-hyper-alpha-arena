"""AI因子: 影线密度均值回归 | 置信:58% | 当插针密度(上下影线相对实体)处于高位时，市场处于情绪化波动环境，价格倾向均值回归。用影线密度分位与短期收益反向交互，捕捉高波动环境下的反转alpha。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class WickDensityMeanReversion(BaseFactor):
    """当插针密度(上下影线相对实体)处于高位时，市场处于情绪化波动环境，价格倾向均值回归。用影线密度分位与短期收益反向交互，捕捉高波动环境下的反转alpha。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_wick_density_reversion",
            name="Wick Density Mean Reversion",
            display_name="影线密度均值回归",
            description="当插针密度(上下影线相对实体)处于高位时，市场处于情绪化波动环境，价格倾向均值回归。用影线密度分位与短期收益反向交互，捕捉高波动环境下的反转alpha。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        body = (data['close'] - data['open']).abs() + 1e-9
        upper = data['high'] - data[['open','close']].max(axis=1)
        lower = data[['open','close']].min(axis=1) - data['low']
        density = (data[['high','low']].max(axis=1) - data[['high','low']].min(axis=1)) / body
        dens_ma = density.rolling(20).mean()
        ret = data['close'].pct_change(5)
        result = (-ret * dens_ma).rolling(3).mean().clip(-1, 1)
        return result
