"""AI因子: 动量加速度 | 置信:60% | 短期动量与中期动量之差衡量动量加速：当短期收益显著强于中期收益时，表明趋势正在加速，未来延续概率高；反之减速则看空。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MomentumAcceleration(BaseFactor):
    """短期动量与中期动量之差衡量动量加速：当短期收益显著强于中期收益时，表明趋势正在加速，未来延续概率高；反之减速则看空。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_mom_accel",
            name="Momentum Acceleration",
            display_name="动量加速度",
            description="短期动量与中期动量之差衡量动量加速：当短期收益显著强于中期收益时，表明趋势正在加速，未来延续概率高；反之减速则看空。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        fast = data['close'].pct_change(5)
        slow = data['close'].pct_change(20)
        vol = data['close'].pct_change().rolling(20).std() + 1e-9
        result = ((fast - slow) / vol).clip(-1, 1)
        return result
