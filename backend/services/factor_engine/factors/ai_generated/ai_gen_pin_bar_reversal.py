"""AI因子: 插针影线不对称反转 | 置信:62% | 计算上下影线不对称度(下影-上影)/实体，反映买方防守vs卖方拒绝的短线力量对比，用rolling均值平滑后作为反转alpha，正不对称(下影主导)预示反弹。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PinBarAsymmetryReversal(BaseFactor):
    """计算上下影线不对称度(下影-上影)/实体，反映买方防守vs卖方拒绝的短线力量对比，用rolling均值平滑后作为反转alpha，正不对称(下影主导)预示反弹。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pin_bar_reversal",
            name="Pin Bar Asymmetry Reversal",
            display_name="插针影线不对称反转",
            description="计算上下影线不对称度(下影-上影)/实体，反映买方防守vs卖方拒绝的短线力量对比，用rolling均值平滑后作为反转alpha，正不对称(下影主导)预示反弹。",
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
