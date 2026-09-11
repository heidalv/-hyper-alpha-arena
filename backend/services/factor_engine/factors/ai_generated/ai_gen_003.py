"""AI因子: 波动调整动量加速度 | 置信:60% | 结合多周期动量差值与波动率调整，捕捉动量加速或减速的转折点。当短期动量相对长期动量加速且波动率较低时，趋势可能启动；当动量减速且波动率上升时，趋势可能衰竭。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityAdjustedMomentumAcceleration(BaseFactor):
    """结合多周期动量差值与波动率调整，捕捉动量加速或减速的转折点。当短期动量相对长期动量加速且波动率较低时，趋势可能启动；当动量减速且波动率上升时，趋势可能衰竭。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_003",
            name="Volatility_Adjusted_Momentum_Acceleration",
            display_name="波动调整动量加速度",
            description="结合多周期动量差值与波动率调整，捕捉动量加速或减速的转折点。当短期动量相对长期动量加速且波动率较低时，趋势可能启动；当动量减速且波动率上升时，趋势可能衰竭。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ret_short = data['close'].pct_change(5)
        ret_long = data['close'].pct_change(20)
        mom_diff = ret_short - ret_long
        vol = data['close'].pct_change().rolling(20).std()
        result = (mom_diff / (vol + 1e-9)).clip(-1, 1)
        return result
