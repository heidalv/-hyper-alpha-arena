"""AI因子: 插针影线不对称反转 | 置信:62% | 以(下影-上影)/实体衡量买卖双方防守强度，短期滚动均值反映插针承接方向，与近5日收益方向交互，捕捉插针后的均值回归alpha。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PinBarAsymmetryReversal5(BaseFactor):
    """以(下影-上影)/实体衡量买卖双方防守强度，短期滚动均值反映插针承接方向，与近5日收益方向交互，捕捉插针后的均值回归alpha。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pin_bar_reversal_5",
            name="Pin Bar Asymmetry Reversal 5",
            display_name="插针影线不对称反转",
            description="以(下影-上影)/实体衡量买卖双方防守强度，短期滚动均值反映插针承接方向，与近5日收益方向交互，捕捉插针后的均值回归alpha。",
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
        ret5 = data['close'].pct_change(5)
        result = (asym.rolling(3).mean() * (1 - ret5.rank(pct=True))).clip(-1, 1)
        return result
