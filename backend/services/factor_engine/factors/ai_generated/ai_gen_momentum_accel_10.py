"""AI因子: 动量加速度10 | 置信:60% | 短期5期动量与中期20期动量之差，衡量动量加速度。正值表示近期动量强于中期趋势，预示趋势延续；负值预示动能衰减。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MomentumAcceleration10(BaseFactor):
    """短期5期动量与中期20期动量之差，衡量动量加速度。正值表示近期动量强于中期趋势，预示趋势延续；负值预示动能衰减。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_momentum_accel_10",
            name="Momentum Acceleration 10",
            display_name="动量加速度10",
            description="短期5期动量与中期20期动量之差，衡量动量加速度。正值表示近期动量强于中期趋势，预示趋势延续；负值预示动能衰减。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        mom5 = data['close'].pct_change(5)
        mom20 = data['close'].pct_change(20)
        vol = data['close'].pct_change().rolling(20).std() + 1e-9
        result = ((mom5 - mom20) / vol).clip(-1, 1)
        return result
