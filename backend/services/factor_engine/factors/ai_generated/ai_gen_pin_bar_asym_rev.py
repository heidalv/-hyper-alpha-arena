"""AI因子: 插针影线不对称反转 | 置信:62% | 利用上下影线不对称度衡量买卖双方防守强度。下影线主导（lower>upper）代表买方承接，未来短期反弹概率高；上影线主导代表卖方拒绝，未来回落概率高。对不对称度做短期平滑后取反号，捕捉插针后的均值回归方向。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PinBarAsymmetryReversal(BaseFactor):
    """利用上下影线不对称度衡量买卖双方防守强度。下影线主导（lower>upper）代表买方承接，未来短期反弹概率高；上影线主导代表卖方拒绝，未来回落概率高。对不对称度做短期平滑后取反号，捕捉插针后的均值回归方向。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pin_bar_asym_rev",
            name="Pin Bar Asymmetry Reversal",
            display_name="插针影线不对称反转",
            description="利用上下影线不对称度衡量买卖双方防守强度。下影线主导（lower>upper）代表买方承接，未来短期反弹概率高；上影线主导代表卖方拒绝，未来回落概率高。对不对称度做短期平滑后取反号，捕捉插针后的均值回归方向。",
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
        result = asym.rolling(3).mean().clip(-1, 1)
        return result
