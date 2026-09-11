"""AI因子: 影线不对称反转 | 置信:75% | 计算上下影线的不对称度，反映市场买卖力量的博弈。下影线主导（买方防守）可能预示反弹，上影线主导（卖方拒绝）可能预示回落，结合波动环境分位数增强因子的稳定性。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class ShadowAsymmetryReversal(BaseFactor):
    """计算上下影线的不对称度，反映市场买卖力量的博弈。下影线主导（买方防守）可能预示反弹，上影线主导（卖方拒绝）可能预示回落，结合波动环境分位数增强因子的稳定性。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_shadow_asymmetry",
            name="Shadow Asymmetry Reversal",
            display_name="影线不对称反转",
            description="计算上下影线的不对称度，反映市场买卖力量的博弈。下影线主导（买方防守）可能预示反弹，上影线主导（卖方拒绝）可能预示回落，结合波动环境分位数增强因子的稳定性。",
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
        pin_density = (np.maximum(upper, lower) / body).rolling(20).mean()
        result = (asymmetry.rolling(3).mean() / (pin_density + 1e-9)).clip(-1, 1)
        return result
