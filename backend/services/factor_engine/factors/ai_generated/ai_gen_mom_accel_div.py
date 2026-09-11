"""AI因子: 动量加速度背离 | 置信:60% | 短期动量与中期动量之差衡量动量加速度，再以已实现波动率归一化，捕捉趋势加速的持续性；当短期动量显著强于中期动量时因子为正，预示趋势延续概率更高。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MomentumAccelerationDivergence(BaseFactor):
    """短期动量与中期动量之差衡量动量加速度，再以已实现波动率归一化，捕捉趋势加速的持续性；当短期动量显著强于中期动量时因子为正，预示趋势延续概率更高。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_mom_accel_div",
            name="Momentum Acceleration Divergence",
            display_name="动量加速度背离",
            description="短期动量与中期动量之差衡量动量加速度，再以已实现波动率归一化，捕捉趋势加速的持续性；当短期动量显著强于中期动量时因子为正，预示趋势延续概率更高。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        short = data['close'].pct_change(5)
        long = data['close'].pct_change(20)
        vol = data['close'].pct_change().rolling(20).std() + 1e-9
        result = ((short - long) / vol).clip(-1, 1)
        return result
