"""AI因子: 插针影线不对称反转 | 置信:62% | 通过下影线与上影线的相对大小衡量买卖双方防守强度。下影主导(买方承接)预示短期反弹，上影主导(卖方拒绝)预示回落。用3期滚动均值平滑噪声，输出[-1,1]方向信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PinBarAsymmetryReversal(BaseFactor):
    """通过下影线与上影线的相对大小衡量买卖双方防守强度。下影主导(买方承接)预示短期反弹，上影主导(卖方拒绝)预示回落。用3期滚动均值平滑噪声，输出[-1,1]方向信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pin_reversal_1",
            name="Pin Bar Asymmetry Reversal",
            display_name="插针影线不对称反转",
            description="通过下影线与上影线的相对大小衡量买卖双方防守强度。下影主导(买方承接)预示短期反弹，上影主导(卖方拒绝)预示回落。用3期滚动均值平滑噪声，输出[-1,1]方向信号。",
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
