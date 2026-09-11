"""AI因子: 插针影线不对称反转 | 置信:62% | 用下影线与上影线的相对长度衡量买卖方防守强度，下影主导（买方承接）预示后续反弹，上影主导（卖方拒绝）预示回落。对不对称度做短周期平滑后取反号，捕捉插针后的短线均值回归。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PinBarAsymmetryReversal(BaseFactor):
    """用下影线与上影线的相对长度衡量买卖方防守强度，下影主导（买方承接）预示后续反弹，上影主导（卖方拒绝）预示回落。对不对称度做短周期平滑后取反号，捕捉插针后的短线均值回归。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pin_bar_asym_rev",
            name="Pin Bar Asymmetry Reversal",
            display_name="插针影线不对称反转",
            description="用下影线与上影线的相对长度衡量买卖方防守强度，下影主导（买方承接）预示后续反弹，上影主导（卖方拒绝）预示回落。对不对称度做短周期平滑后取反号，捕捉插针后的短线均值回归。",
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
        result = (-asym.rolling(3).mean()).clip(-1, 1)
        return result
