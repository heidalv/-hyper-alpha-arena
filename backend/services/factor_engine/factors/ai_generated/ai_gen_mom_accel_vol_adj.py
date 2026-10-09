"""AI因子: 波动调整动量加速度 | 置信:60% | 多周期动量差（短周期收益减长周期收益）刻画动量加速度，再除以已实现波动率进行风险调整。正值表示短期动量强于长期趋势，未来延续上涨概率更高；负值反之。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MomentumAccelerationVolAdjusted(BaseFactor):
    """多周期动量差（短周期收益减长周期收益）刻画动量加速度，再除以已实现波动率进行风险调整。正值表示短期动量强于长期趋势，未来延续上涨概率更高；负值反之。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_mom_accel_vol_adj",
            name="Momentum Acceleration Vol Adjusted",
            display_name="波动调整动量加速度",
            description="多周期动量差（短周期收益减长周期收益）刻画动量加速度，再除以已实现波动率进行风险调整。正值表示短期动量强于长期趋势，未来延续上涨概率更高；负值反之。",
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
