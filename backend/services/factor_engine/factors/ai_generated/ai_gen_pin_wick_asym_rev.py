"""AI因子: 插针影线不对称反转 | 置信:62% | 长下影线代表买方防守（承接），长上影线代表卖方拒绝。用(lower-upper)/body衡量影线不对称度，短期rolling均值捕捉插针反转方向；下影主导时看涨，上影主导时看跌。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PinWickAsymmetryReversal(BaseFactor):
    """长下影线代表买方防守（承接），长上影线代表卖方拒绝。用(lower-upper)/body衡量影线不对称度，短期rolling均值捕捉插针反转方向；下影主导时看涨，上影主导时看跌。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pin_wick_asym_rev",
            name="Pin Wick Asymmetry Reversal",
            display_name="插针影线不对称反转",
            description="长下影线代表买方防守（承接），长上影线代表卖方拒绝。用(lower-upper)/body衡量影线不对称度，短期rolling均值捕捉插针反转方向；下影主导时看涨，上影主导时看跌。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        body = (data['close'] - data['open']).abs() + 1e-9
        upper = data['high'] - data[['open','close']].max(axis=1)
        lower = data[['open','close']].min(axis=1) - data['low']
        result = ((lower - upper) / body).rolling(3).mean().clip(-1, 1)
        return result
