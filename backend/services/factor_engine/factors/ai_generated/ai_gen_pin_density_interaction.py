"""AI因子: 插针密度加权反转 | 置信:58% | 用上下影线相对实体的均值(插针密度)作为波动环境权重，与短期收益方向交互，捕捉高插针环境下短期收益的均值回归。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PinDensityWeightedReversal(BaseFactor):
    """用上下影线相对实体的均值(插针密度)作为波动环境权重，与短期收益方向交互，捕捉高插针环境下短期收益的均值回归。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pin_density_interaction",
            name="Pin Density Weighted Reversal",
            display_name="插针密度加权反转",
            description="用上下影线相对实体的均值(插针密度)作为波动环境权重，与短期收益方向交互，捕捉高插针环境下短期收益的均值回归。",
            category="technical",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        body = (data['close'] - data['open']).abs() + 1e-9
        upper = data['high'] - data[['open','close']].max(axis=1)
        lower = data[['open','close']].min(axis=1) - data['low']
        density = (np.maximum(upper, lower) / body).rolling(20).mean()
        ret = data['close'].pct_change(3)
        result = (-ret * density).clip(-1, 1)
        return result
