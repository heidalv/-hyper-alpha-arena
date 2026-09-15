"""AI因子: 短期影线不对称反转 | 置信:62% | 利用上下影线不对称度（下影主导=买方防守，上影主导=卖方拒绝）的短期均值与收益方向交互，捕捉插针后的短线反转。下影线主导时因子为正（看涨），上影线主导时为负（看跌）。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class ShortTermWickAsymmetryReversal(BaseFactor):
    """利用上下影线不对称度（下影主导=买方防守，上影主导=卖方拒绝）的短期均值与收益方向交互，捕捉插针后的短线反转。下影线主导时因子为正（看涨），上影线主导时为负（看跌）。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_wick_reversal_short",
            name="Short-Term Wick Asymmetry Reversal",
            display_name="短期影线不对称反转",
            description="利用上下影线不对称度（下影主导=买方防守，上影主导=卖方拒绝）的短期均值与收益方向交互，捕捉插针后的短线反转。下影线主导时因子为正（看涨），上影线主导时为负（看跌）。",
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
        result = (asym.rolling(3).mean() * (1 - ret.rolling(3).mean().rank(pct=True))).clip(-1, 1)
        return result
