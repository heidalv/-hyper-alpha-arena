"""AI因子: 插针影线不对称反转 | 置信:62% | 计算上下影线不对称度（下影主导为买方防守，上影主导为卖方拒绝），取短期滚动均值后与短期收益方向交互，捕捉插针后的均值回归 alpha。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PinBarAsymmetryReversal(BaseFactor):
    """计算上下影线不对称度（下影主导为买方防守，上影主导为卖方拒绝），取短期滚动均值后与短期收益方向交互，捕捉插针后的均值回归 alpha。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pin_bar_asym_rev",
            name="Pin Bar Asymmetry Reversal",
            display_name="插针影线不对称反转",
            description="计算上下影线不对称度（下影主导为买方防守，上影主导为卖方拒绝），取短期滚动均值后与短期收益方向交互，捕捉插针后的均值回归 alpha。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        body = (data['close'] - data['open']).abs() + 1e-9
        upper = data['high'] - data[['open','close']].max(axis=1)
        lower = data[['open','close']].min(axis=1) - data['low']
        asym = ((lower - upper) / body).rolling(3).mean()
        ret = data['close'].pct_change(3)
        result = (asym * (-ret)).rolling(5).mean().clip(-1, 1)
        return result
