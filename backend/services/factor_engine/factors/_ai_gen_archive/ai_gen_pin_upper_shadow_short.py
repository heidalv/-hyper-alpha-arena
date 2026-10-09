"""AI因子: 上影线拒绝回落因子 | 置信:60% | 长上影线代表卖方在高位拒绝，若伴随近期上涨动能则回落概率更高。用上影主导度与短期动量交互，捕捉插针后的均值回归。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class UpperShadowRejectionMomentum(BaseFactor):
    """长上影线代表卖方在高位拒绝，若伴随近期上涨动能则回落概率更高。用上影主导度与短期动量交互，捕捉插针后的均值回归。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pin_upper_shadow_short",
            name="Upper Shadow Rejection Momentum",
            display_name="上影线拒绝回落因子",
            description="长上影线代表卖方在高位拒绝，若伴随近期上涨动能则回落概率更高。用上影主导度与短期动量交互，捕捉插针后的均值回归。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        body = (data['close'] - data['open']).abs() + 1e-9
        upper = data['high'] - data[['open','close']].max(axis=1)
        lower = data[['open','close']].min(axis=1) - data['low']
        asym = (upper - lower) / body
        mom = data['close'].pct_change(10)
        result = (asym.rolling(3).mean() * mom.clip(-1, 1)).rolling(5).mean().clip(-1, 1)
        return result
