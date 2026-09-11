"""AI因子: 影线反转密度因子 | 置信:70% | 结合上下影线不对称度与短期收益方向，捕捉插针后的均值回归机会。当影线不对称度（下影主导为正）与近期负收益交互时，预示买方承接后反弹；上影主导与正收益交互时预示卖方拒绝后回落。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class WickReversalDensity(BaseFactor):
    """结合上下影线不对称度与短期收益方向，捕捉插针后的均值回归机会。当影线不对称度（下影主导为正）与近期负收益交互时，预示买方承接后反弹；上影主导与正收益交互时预示卖方拒绝后回落。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_wick_reversal_density",
            name="Wick_Reversal_Density",
            display_name="影线反转密度因子",
            description="结合上下影线不对称度与短期收益方向，捕捉插针后的均值回归机会。当影线不对称度（下影主导为正）与近期负收益交互时，预示买方承接后反弹；上影主导与正收益交互时预示卖方拒绝后回落。",
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
        asym_smooth = asym.rolling(3).mean()
        ret_short = data['close'].pct_change(3)
        result = (asym_smooth * (-ret_short)).clip(-1, 1)
        return result
