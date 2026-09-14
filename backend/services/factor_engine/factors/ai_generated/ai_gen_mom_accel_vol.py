"""AI因子: 动量加速度波动调整 | 置信:60% | 多周期动量差(5日减20日收益)衡量动量加速度：正值表示短期动量强于长期，趋势加速上行；负值表示动量衰减。除以已实现波动率做风险调整，提升信噪比，预测未来收益方向。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MomentumAccelerationVolatilityAdjusted(BaseFactor):
    """多周期动量差(5日减20日收益)衡量动量加速度：正值表示短期动量强于长期，趋势加速上行；负值表示动量衰减。除以已实现波动率做风险调整，提升信噪比，预测未来收益方向。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_mom_accel_vol",
            name="Momentum Acceleration Volatility Adjusted",
            display_name="动量加速度波动调整",
            description="多周期动量差(5日减20日收益)衡量动量加速度：正值表示短期动量强于长期，趋势加速上行；负值表示动量衰减。除以已实现波动率做风险调整，提升信噪比，预测未来收益方向。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        mom5 = data['close'].pct_change(5)
        mom20 = data['close'].pct_change(20)
        accel = mom5 - mom20
        vol = data['close'].pct_change().rolling(20).std()
        result = (accel / (vol + 1e-9)).clip(-1, 1)
        return result
