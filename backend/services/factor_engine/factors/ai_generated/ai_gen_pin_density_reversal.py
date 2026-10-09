"""AI因子: 插针密度加权反转 | 置信:58% | 插针密度(上下影线相对实体的均值)衡量市场影线密集环境。在影线密集环境下，短期收益方向更易反转。用密度分位加权短期收益的反向，捕捉插针环境下的均值回归alpha。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PinDensityWeightedReversal(BaseFactor):
    """插针密度(上下影线相对实体的均值)衡量市场影线密集环境。在影线密集环境下，短期收益方向更易反转。用密度分位加权短期收益的反向，捕捉插针环境下的均值回归alpha。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pin_density_reversal",
            name="Pin Density Weighted Reversal",
            display_name="插针密度加权反转",
            description="插针密度(上下影线相对实体的均值)衡量市场影线密集环境。在影线密集环境下，短期收益方向更易反转。用密度分位加权短期收益的反向，捕捉插针环境下的均值回归alpha。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        body = (data['close'] - data['open']).abs() + 1e-9
        upper = data['high'] - data[['open','close']].max(axis=1)
        lower = data[['open','close']].min(axis=1) - data['low']
        density = (np.maximum(upper, lower) / body).rolling(20).mean()
        ret = data['close'].pct_change(3)
        result = (-ret * density).rolling(3).mean().clip(-1, 1)
        return result
