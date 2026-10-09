"""AI因子: 插针反转不对称度 | 置信:62% | 用下影线与上影线的不对称度衡量买卖方防守强度。下影主导(lower>upper)表示买方在低位承接，未来反弹概率高；上影主导表示卖方拒绝，未来回落概率高。取3日滚动均值平滑噪声，输出[-1,1]方向性因子。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PinBarReversalAsymmetry(BaseFactor):
    """用下影线与上影线的不对称度衡量买卖方防守强度。下影主导(lower>upper)表示买方在低位承接，未来反弹概率高；上影主导表示卖方拒绝，未来回落概率高。取3日滚动均值平滑噪声，输出[-1,1]方向性因子。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pin_reversal_20",
            name="Pin Bar Reversal Asymmetry",
            display_name="插针反转不对称度",
            description="用下影线与上影线的不对称度衡量买卖方防守强度。下影主导(lower>upper)表示买方在低位承接，未来反弹概率高；上影主导表示卖方拒绝，未来回落概率高。取3日滚动均值平滑噪声，输出[-1,1]方向性因子。",
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
