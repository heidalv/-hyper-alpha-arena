"""AI因子: 波动归一化动量加速度 | 置信:60% | 多周期动量差(5日减20日收益)衡量动量加速度，除以20日已实现波动率做归一化，捕捉趋势加速或衰竭信号。高值代表短期动量显著强于中期，未来延续上涨概率更高。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MomentumAccelerationVolNormalized(BaseFactor):
    """多周期动量差(5日减20日收益)衡量动量加速度，除以20日已实现波动率做归一化，捕捉趋势加速或衰竭信号。高值代表短期动量显著强于中期，未来延续上涨概率更高。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_momentum_accel_volnorm",
            name="Momentum Acceleration Vol-Normalized",
            display_name="波动归一化动量加速度",
            description="多周期动量差(5日减20日收益)衡量动量加速度，除以20日已实现波动率做归一化，捕捉趋势加速或衰竭信号。高值代表短期动量显著强于中期，未来延续上涨概率更高。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        mom_fast = data['close'].pct_change(5)
        mom_slow = data['close'].pct_change(20)
        vol = data['close'].pct_change().rolling(20).std() + 1e-9
        result = ((mom_fast - mom_slow) / vol).clip(-1, 1)
        return result
