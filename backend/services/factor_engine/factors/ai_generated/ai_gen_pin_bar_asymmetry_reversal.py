"""AI因子: 插针影线不对称反转 | 置信:60% | 利用下影线主导（买方防守）与上影线主导（卖方拒绝）的不对称度，结合短期收益方向做均值回归。下影主导且近期下跌时看多，上影主导且近期上涨时看空。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PinBarAsymmetryReversal(BaseFactor):
    """利用下影线主导（买方防守）与上影线主导（卖方拒绝）的不对称度，结合短期收益方向做均值回归。下影主导且近期下跌时看多，上影主导且近期上涨时看空。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pin_bar_asymmetry_reversal",
            name="Pin Bar Asymmetry Reversal",
            display_name="插针影线不对称反转",
            description="利用下影线主导（买方防守）与上影线主导（卖方拒绝）的不对称度，结合短期收益方向做均值回归。下影主导且近期下跌时看多，上影主导且近期上涨时看空。",
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
        ret = data['close'].pct_change(3)
        result = (asym.rolling(3).mean() * (-ret).apply(lambda x: 1 if x > 0 else -1)).clip(-1, 1)
        return result
