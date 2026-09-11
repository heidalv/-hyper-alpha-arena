"""AI因子: 多周期动量加速度 | 置信:60% | 短期动量减去长期动量，衡量动量加速度。当短期收益显著强于长期收益时，说明趋势正在加速，未来延续概率高；反之则可能反转。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MultiPeriodMomentumAcceleration(BaseFactor):
    """短期动量减去长期动量，衡量动量加速度。当短期收益显著强于长期收益时，说明趋势正在加速，未来延续概率高；反之则可能反转。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_mom_accel",
            name="Multi-Period Momentum Acceleration",
            display_name="多周期动量加速度",
            description="短期动量减去长期动量，衡量动量加速度。当短期收益显著强于长期收益时，说明趋势正在加速，未来延续概率高；反之则可能反转。",
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
