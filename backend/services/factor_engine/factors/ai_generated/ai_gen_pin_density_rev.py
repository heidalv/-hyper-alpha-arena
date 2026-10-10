"""AI因子: 插针密度加权反转 | 置信:55% | 在高插针密度（影线主导）的波动环境中，影线不对称度的均值回归信号更可靠。用20日插针密度作为环境权重，与短期影线不对称度交互，放大插针反转alpha。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PinDensityWeightedReversal(BaseFactor):
    """在高插针密度（影线主导）的波动环境中，影线不对称度的均值回归信号更可靠。用20日插针密度作为环境权重，与短期影线不对称度交互，放大插针反转alpha。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pin_density_rev",
            name="Pin Density Weighted Reversal",
            display_name="插针密度加权反转",
            description="在高插针密度（影线主导）的波动环境中，影线不对称度的均值回归信号更可靠。用20日插针密度作为环境权重，与短期影线不对称度交互，放大插针反转alpha。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        body = (data['close'] - data['open']).abs() + 1e-9
        upper = data['high'] - data[['open','close']].max(axis=1)
        lower = data[['open','close']].min(axis=1) - data['low']
        asym = (lower - upper) / body
        density = (data[['open','close']].max(axis=1) - data[['open','close']].min(axis=1)).rolling(20).mean()
        result = (asym.rolling(5).mean() / (data['close'].pct_change().rolling(20).std() + 1e-9)).clip(-1, 1)
        return result
