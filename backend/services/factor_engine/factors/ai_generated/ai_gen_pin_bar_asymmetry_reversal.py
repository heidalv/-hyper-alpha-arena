"""AI因子: 插针影线不对称反转 | 置信:62% | 利用上下影线不对称度衡量买卖双方在极值处的防守强度。下影主导（买方承接）后短期反弹概率高，上影主导（卖方拒绝）后短期回落概率高。用3日滚动均值平滑单根噪声，并与短期收益方向交互，捕捉插针后的均值回归 alpha。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PinBarAsymmetryReversal(BaseFactor):
    """利用上下影线不对称度衡量买卖双方在极值处的防守强度。下影主导（买方承接）后短期反弹概率高，上影主导（卖方拒绝）后短期回落概率高。用3日滚动均值平滑单根噪声，并与短期收益方向交互，捕捉插针后的均值回归 alpha。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pin_bar_asymmetry_reversal",
            name="Pin Bar Asymmetry Reversal",
            display_name="插针影线不对称反转",
            description="利用上下影线不对称度衡量买卖双方在极值处的防守强度。下影主导（买方承接）后短期反弹概率高，上影主导（卖方拒绝）后短期回落概率高。用3日滚动均值平滑单根噪声，并与短期收益方向交互，捕捉插针后的均值回归 alpha。",
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
        smooth = asym.rolling(3).mean()
        ret = data['close'].pct_change(3)
        result = (smooth - ret * 10).clip(-1, 1)
        return result
