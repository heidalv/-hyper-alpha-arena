"""AI因子: 插针影线不对称反转 | 置信:62% | 用上下影线相对实体的不对称度衡量买卖方防守强度：下影主导(lower>upper)代表买方承接，未来反弹概率高；上影主导代表卖方拒绝，未来回落概率高。对不对称度做3日平滑并与短期收益方向交互，捕捉插针后的均值回归alpha。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PinBarAsymmetryReversal(BaseFactor):
    """用上下影线相对实体的不对称度衡量买卖方防守强度：下影主导(lower>upper)代表买方承接，未来反弹概率高；上影主导代表卖方拒绝，未来回落概率高。对不对称度做3日平滑并与短期收益方向交互，捕捉插针后的均值回归alpha。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pin_bar_reversal_v1",
            name="Pin Bar Asymmetry Reversal",
            display_name="插针影线不对称反转",
            description="用上下影线相对实体的不对称度衡量买卖方防守强度：下影主导(lower>upper)代表买方承接，未来反弹概率高；上影主导代表卖方拒绝，未来回落概率高。对不对称度做3日平滑并与短期收益方向交互，捕捉插针后的均值回归alpha。",
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
        ret = data['close'].pct_change(3)
        result = (asym.rolling(3).mean() * (1 - ret.rank(pct=True))).clip(-1, 1)
        return result
