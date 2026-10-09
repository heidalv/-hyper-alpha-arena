"""AI因子: 影线不对称反转 | 置信:62% | 长下影线代表买方防守承接，长上影线代表卖方拒绝。用(lower-upper)/body衡量影线不对称度，滚动平滑后与短期收益方向交互，捕捉插针后的均值回归alpha。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class WickAsymmetryReversal(BaseFactor):
    """长下影线代表买方防守承接，长上影线代表卖方拒绝。用(lower-upper)/body衡量影线不对称度，滚动平滑后与短期收益方向交互，捕捉插针后的均值回归alpha。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_wick_reversal_5",
            name="Wick Asymmetry Reversal",
            display_name="影线不对称反转",
            description="长下影线代表买方防守承接，长上影线代表卖方拒绝。用(lower-upper)/body衡量影线不对称度，滚动平滑后与短期收益方向交互，捕捉插针后的均值回归alpha。",
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
        result = (asym.rolling(5).mean() * (-ret).apply(lambda x: 1 if x > 0 else -1)).clip(-1, 1)
        return result
