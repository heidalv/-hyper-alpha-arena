"""AI因子: 插针影线不对称反转 | 置信:62% | 计算K线上下影线不对称度（下影主导为买方防守，上影主导为卖方拒绝），对近3根K线取均值平滑，并与短期收益方向交互，捕捉插针后的短线均值回归alpha。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PinBarAsymmetryReversal(BaseFactor):
    """计算K线上下影线不对称度（下影主导为买方防守，上影主导为卖方拒绝），对近3根K线取均值平滑，并与短期收益方向交互，捕捉插针后的短线均值回归alpha。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pin_bar_asymmetry_reversal",
            name="Pin Bar Asymmetry Reversal",
            display_name="插针影线不对称反转",
            description="计算K线上下影线不对称度（下影主导为买方防守，上影主导为卖方拒绝），对近3根K线取均值平滑，并与短期收益方向交互，捕捉插针后的短线均值回归alpha。",
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
        result = (asym.rolling(3).mean() * (-ret).rolling(3).mean()).clip(-1, 1)
        return result
