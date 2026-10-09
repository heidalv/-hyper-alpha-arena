"""AI因子: 插针不对称反转 | 置信:62% | 利用下影线与上影线的相对长度衡量买卖双方防守强度。下影主导（买方承接强）预示短期反弹，上影主导（卖方拒绝强）预示短期回落。对不对称度做短期平滑后取反方向，捕捉插针后的均值回归 alpha。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PinBarAsymmetryReversal(BaseFactor):
    """利用下影线与上影线的相对长度衡量买卖双方防守强度。下影主导（买方承接强）预示短期反弹，上影主导（卖方拒绝强）预示短期回落。对不对称度做短期平滑后取反方向，捕捉插针后的均值回归 alpha。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pin_reversal_asym",
            name="Pin Bar Asymmetry Reversal",
            display_name="插针不对称反转",
            description="利用下影线与上影线的相对长度衡量买卖双方防守强度。下影主导（买方承接强）预示短期反弹，上影主导（卖方拒绝强）预示短期回落。对不对称度做短期平滑后取反方向，捕捉插针后的均值回归 alpha。",
            category="technical",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        body = (data['close'] - data['open']).abs() + 1e-9
        upper = data['high'] - data[['open','close']].max(axis=1)
        lower = data[['open','close']].min(axis=1) - data['low']
        asym = (lower - upper) / body
        result = asym.rolling(3).mean().clip(-1, 1)
        return result
