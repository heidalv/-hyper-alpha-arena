"""AI因子: 插针不对称反转 | 置信:60% | 用下影线与上影线的不对称度衡量买卖方防守强度，rolling 平滑后作为短线均值回归信号：下影主导(买方承接)预示反弹，上影主导(卖方拒绝)预示回落。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PinBarAsymmetryReversal(BaseFactor):
    """用下影线与上影线的不对称度衡量买卖方防守强度，rolling 平滑后作为短线均值回归信号：下影主导(买方承接)预示反弹，上影主导(卖方拒绝)预示回落。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pin_reversal_asym",
            name="Pin Bar Asymmetry Reversal",
            display_name="插针不对称反转",
            description="用下影线与上影线的不对称度衡量买卖方防守强度，rolling 平滑后作为短线均值回归信号：下影主导(买方承接)预示反弹，上影主导(卖方拒绝)预示回落。",
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
        result = asym.rolling(5).mean().clip(-1, 1)
        return result
