"""AI因子: 插针反转不对称度5 | 置信:62% | 利用上下影线不对称度衡量买卖双方防守强度：下影主导（买方承接）预示反弹，上影主导（卖方拒绝）预示回落。用5周期滚动均值平滑噪音，输出[-1,1]方向信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PinBarReversalAsymmetry5(BaseFactor):
    """利用上下影线不对称度衡量买卖双方防守强度：下影主导（买方承接）预示反弹，上影主导（卖方拒绝）预示回落。用5周期滚动均值平滑噪音，输出[-1,1]方向信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pin_reversal_5",
            name="Pin Bar Reversal Asymmetry 5",
            display_name="插针反转不对称度5",
            description="利用上下影线不对称度衡量买卖双方防守强度：下影主导（买方承接）预示反弹，上影主导（卖方拒绝）预示回落。用5周期滚动均值平滑噪音，输出[-1,1]方向信号。",
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
        result = asym.rolling(5).mean().clip(-1, 1)
        return result
