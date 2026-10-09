"""AI因子: 插针反转不对称度 | 置信:62% | 基于上下影线不对称度(lower-upper)/body的短期均值，捕捉买方防守(下影主导)后的反弹与卖方拒绝(上影主导)后的回落。用3期滚动平滑降低噪声，clip到[-1,1]作为方向性alpha。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PinbarReversalAsymmetry(BaseFactor):
    """基于上下影线不对称度(lower-upper)/body的短期均值，捕捉买方防守(下影主导)后的反弹与卖方拒绝(上影主导)后的回落。用3期滚动平滑降低噪声，clip到[-1,1]作为方向性alpha。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pinbar_reversal_asym",
            name="Pinbar Reversal Asymmetry",
            display_name="插针反转不对称度",
            description="基于上下影线不对称度(lower-upper)/body的短期均值，捕捉买方防守(下影主导)后的反弹与卖方拒绝(上影主导)后的回落。用3期滚动平滑降低噪声，clip到[-1,1]作为方向性alpha。",
            category="technical",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        body = (data['close'] - data['open']).abs() + 1e-9
        upper = data['high'] - data[['open','close']].max(axis=1)
        lower = data[['open','close']].min(axis=1) - data['low']
        result = ((lower - upper) / body).rolling(3).mean().clip(-1, 1)
        return result
