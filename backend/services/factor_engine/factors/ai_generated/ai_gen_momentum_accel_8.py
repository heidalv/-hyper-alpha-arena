"""AI因子: 动量加速度因子 | 置信:60% | 用短周期动量减去长周期动量衡量动量加速度：短周期显著强于长周期说明趋势正在加速，未来延续上涨概率高；反之动量衰减预示反转。该因子捕捉趋势拐点前的加速度变化。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MomentumAcceleration8(BaseFactor):
    """用短周期动量减去长周期动量衡量动量加速度：短周期显著强于长周期说明趋势正在加速，未来延续上涨概率高；反之动量衰减预示反转。该因子捕捉趋势拐点前的加速度变化。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_momentum_accel_8",
            name="Momentum Acceleration 8",
            display_name="动量加速度因子",
            description="用短周期动量减去长周期动量衡量动量加速度：短周期显著强于长周期说明趋势正在加速，未来延续上涨概率高；反之动量衰减预示反转。该因子捕捉趋势拐点前的加速度变化。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        short_mom = data['close'].pct_change(5)
        long_mom = data['close'].pct_change(20)
        vol = data['close'].pct_change().rolling(20).std() + 1e-9
        result = ((short_mom - long_mom) / vol).clip(-1, 1)
        return result
