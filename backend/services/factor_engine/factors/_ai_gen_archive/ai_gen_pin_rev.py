"""AI因子: 插针反转因子 | 置信:75% | 基于K线影线不对称度的滚动均值与短期收益方向的交互，捕捉影线反转后的均值回归机会。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Pinreversalfactor(BaseFactor):
    """基于K线影线不对称度的滚动均值与短期收益方向的交互，捕捉影线反转后的均值回归机会。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pin_rev",
            name="PinReversalFactor",
            display_name="插针反转因子",
            description="基于K线影线不对称度的滚动均值与短期收益方向的交互，捕捉影线反转后的均值回归机会。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        body = (data['close'] - data['open']).abs() + 1e-9
        upper = data['high'] - data[['open','close']].max(axis=1)
        lower = data[['open','close']].min(axis=1) - data['low']
        asymmetry = (lower - upper) / body
        asymmetry_mean = asymmetry.rolling(20).mean()
        ret = data['close'].pct_change(5)
        result = (asymmetry_mean * ret).clip(-1, 1)
        return result
