"""AI因子: 动量加速度 | 置信:60% | 短周期收益减去长周期收益的差值，衡量动量加速或衰减，正值代表近期动能强于中期，倾向延续上涨。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MomentumAcceleration(BaseFactor):
    """短周期收益减去长周期收益的差值，衡量动量加速或衰减，正值代表近期动能强于中期，倾向延续上涨。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_mom_accel",
            name="Momentum Acceleration",
            display_name="动量加速度",
            description="短周期收益减去长周期收益的差值，衡量动量加速或衰减，正值代表近期动能强于中期，倾向延续上涨。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        short = data['close'].pct_change(5)
        long = data['close'].pct_change(20)
        vol = data['close'].pct_change().rolling(20).std()
        result = ((short - long) / (vol + 1e-9)).clip(-1, 1)
        return result
