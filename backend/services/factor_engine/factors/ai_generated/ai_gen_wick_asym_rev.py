"""AI因子: 影线不对称反转 | 置信:62% | 利用上下影线不对称度衡量买卖双方在K线内的力量对比：下影主导(买方防守)后倾向反弹，上影主导(卖方拒绝)后倾向回落。对不对称度做短期平滑并与近期收益方向交互，捕捉插针后的均值回归alpha。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class WickAsymmetryReversal(BaseFactor):
    """利用上下影线不对称度衡量买卖双方在K线内的力量对比：下影主导(买方防守)后倾向反弹，上影主导(卖方拒绝)后倾向回落。对不对称度做短期平滑并与近期收益方向交互，捕捉插针后的均值回归alpha。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_wick_asym_rev",
            name="Wick Asymmetry Reversal",
            display_name="影线不对称反转",
            description="利用上下影线不对称度衡量买卖双方在K线内的力量对比：下影主导(买方防守)后倾向反弹，上影主导(卖方拒绝)后倾向回落。对不对称度做短期平滑并与近期收益方向交互，捕捉插针后的均值回归alpha。",
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
        smooth = asym.rolling(5).mean()
        ret = data['close'].pct_change(3)
        result = (smooth - ret * 5).clip(-1, 1)
        return result
