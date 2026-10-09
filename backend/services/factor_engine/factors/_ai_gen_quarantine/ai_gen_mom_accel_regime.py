"""AI因子: 动量加速度 | 置信:58% | 多周期动量差(5日收益减20日收益)捕捉动量加速/减速：短期动量显著强于中期动量时趋势加速延续，反之动量衰竭，用波动率标准化后截断为方向因子。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MomentumAcceleration(BaseFactor):
    """多周期动量差(5日收益减20日收益)捕捉动量加速/减速：短期动量显著强于中期动量时趋势加速延续，反之动量衰竭，用波动率标准化后截断为方向因子。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_mom_accel_regime",
            name="Momentum Acceleration",
            display_name="动量加速度",
            description="多周期动量差(5日收益减20日收益)捕捉动量加速/减速：短期动量显著强于中期动量时趋势加速延续，反之动量衰竭，用波动率标准化后截断为方向因子。",
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
