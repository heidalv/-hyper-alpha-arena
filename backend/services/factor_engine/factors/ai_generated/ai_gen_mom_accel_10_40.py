"""AI因子: 动量加速度差 | 置信:60% | 短期动量与长期动量之差衡量趋势加速，正值代表近期动能强于中期基准，预示延续上涨；负值预示走弱。除以波动率做标准化以跨品种可比。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MomentumAccelerationSpread(BaseFactor):
    """短期动量与长期动量之差衡量趋势加速，正值代表近期动能强于中期基准，预示延续上涨；负值预示走弱。除以波动率做标准化以跨品种可比。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_mom_accel_10_40",
            name="Momentum Acceleration Spread",
            display_name="动量加速度差",
            description="短期动量与长期动量之差衡量趋势加速，正值代表近期动能强于中期基准，预示延续上涨；负值预示走弱。除以波动率做标准化以跨品种可比。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        fast = data['close'].pct_change(5)
        slow = data['close'].pct_change(20)
        vol = data['close'].pct_change().rolling(20).std()
        result = ((fast - slow) / (vol + 1e-9)).clip(-1, 1)
        return result
