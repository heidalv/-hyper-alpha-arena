"""AI因子: 波动率调整动量加速度 | 置信:60% | 短期动量(5日)减去中期动量(20日)得到动量加速度，再除以20日已实现波动率做标准化，正值代表上涨加速，负值代表下跌加速，捕捉趋势延续与转折。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityAdjustedMomentumAcceleration(BaseFactor):
    """短期动量(5日)减去中期动量(20日)得到动量加速度，再除以20日已实现波动率做标准化，正值代表上涨加速，负值代表下跌加速，捕捉趋势延续与转折。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_momentum_accel_vol_adj",
            name="Volatility-Adjusted Momentum Acceleration",
            display_name="波动率调整动量加速度",
            description="短期动量(5日)减去中期动量(20日)得到动量加速度，再除以20日已实现波动率做标准化，正值代表上涨加速，负值代表下跌加速，捕捉趋势延续与转折。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        mom_s = data['close'].pct_change(5)
        mom_m = data['close'].pct_change(20)
        vol = data['close'].pct_change().rolling(20).std()
        result = ((mom_s - mom_m) / (vol + 1e-9)).clip(-1, 1)
        return result
