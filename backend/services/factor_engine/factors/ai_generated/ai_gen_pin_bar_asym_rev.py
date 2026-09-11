"""AI因子: 插针影线不对称反转 | 置信:62% | 利用上下影线不对称度（下影主导=买方防守，上影主导=卖方拒绝）的短期均值，捕捉插针后的反转方向。下影线主导时因子为正，预示反弹；上影线主导时因子为负，预示回落。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PinBarAsymmetryReversal(BaseFactor):
    """利用上下影线不对称度（下影主导=买方防守，上影主导=卖方拒绝）的短期均值，捕捉插针后的反转方向。下影线主导时因子为正，预示反弹；上影线主导时因子为负，预示回落。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pin_bar_asym_rev",
            name="Pin Bar Asymmetry Reversal",
            display_name="插针影线不对称反转",
            description="利用上下影线不对称度（下影主导=买方防守，上影主导=卖方拒绝）的短期均值，捕捉插针后的反转方向。下影线主导时因子为正，预示反弹；上影线主导时因子为负，预示回落。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        body = (data['close'] - data['open']).abs() + 1e-9
        upper = data['high'] - data[['open','close']].max(axis=1)
        lower = data[['open','close']].min(axis=1) - data['low']
        result = ((lower - upper) / body).rolling(3).mean().clip(-1, 1)
        return result
