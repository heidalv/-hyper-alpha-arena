"""AI因子: 波动率调整动量加速度 | 置信:60% | 多周期动量差(5日减20日收益)除以20日已实现波动率，捕捉动量加速/减速的相对强度，高值代表短期动量相对长期显著加速，预示趋势延续。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityAdjustedMomentumAcceleration(BaseFactor):
    """多周期动量差(5日减20日收益)除以20日已实现波动率，捕捉动量加速/减速的相对强度，高值代表短期动量相对长期显著加速，预示趋势延续。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_voladj_mom_accel",
            name="Volatility Adjusted Momentum Acceleration",
            display_name="波动率调整动量加速度",
            description="多周期动量差(5日减20日收益)除以20日已实现波动率，捕捉动量加速/减速的相对强度，高值代表短期动量相对长期显著加速，预示趋势延续。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        mom_fast = data['close'].pct_change(5)
        mom_slow = data['close'].pct_change(20)
        vol = data['close'].pct_change().rolling(20).std()
        result = ((mom_fast - mom_slow) / (vol + 1e-9)).clip(-1, 1)
        return result
