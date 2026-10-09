"""AI因子: 插针下影反转因子 | 置信:62% | 利用K线下影线与上影线的不对称度衡量买方防守强度，下影主导（锤子线）预示短期反弹。对不对称度取3日滚动均值并与短期收益方向交互，捕捉插针后的均值回归alpha。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PinbarLowerShadowReversal(BaseFactor):
    """利用K线下影线与上影线的不对称度衡量买方防守强度，下影主导（锤子线）预示短期反弹。对不对称度取3日滚动均值并与短期收益方向交互，捕捉插针后的均值回归alpha。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pinbar_reversal_5",
            name="Pinbar Lower Shadow Reversal",
            display_name="插针下影反转因子",
            description="利用K线下影线与上影线的不对称度衡量买方防守强度，下影主导（锤子线）预示短期反弹。对不对称度取3日滚动均值并与短期收益方向交互，捕捉插针后的均值回归alpha。",
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
