"""AI因子: 插针不对称反转 | 置信:62% | 用下影线与上影线的不对称度衡量买卖方防守强度，短期滚动均值捕捉插针后的均值回归方向，下影主导预期反弹（因子为正），上影主导预期回落（因子为负）。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PinBarAsymmetryReversal(BaseFactor):
    """用下影线与上影线的不对称度衡量买卖方防守强度，短期滚动均值捕捉插针后的均值回归方向，下影主导预期反弹（因子为正），上影主导预期回落（因子为负）。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pin_reversal_asym",
            name="Pin Bar Asymmetry Reversal",
            display_name="插针不对称反转",
            description="用下影线与上影线的不对称度衡量买卖方防守强度，短期滚动均值捕捉插针后的均值回归方向，下影主导预期反弹（因子为正），上影主导预期回落（因子为负）。",
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
        ret = data['close'].pct_change(3)
        result = (asym.rolling(3).mean() * (1 - ret.rolling(3).mean() * 50)).clip(-1, 1)
        return result
