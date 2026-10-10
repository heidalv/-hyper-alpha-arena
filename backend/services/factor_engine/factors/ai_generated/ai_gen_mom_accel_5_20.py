"""AI因子: 动量加速度5-20 | 置信:58% | 短期5日收益与中期20日收益之差，衡量动量加速度。正值表示近期动量强于中期趋势（加速上涨），负值表示动量衰减或反转。捕捉趋势加速与衰竭的alpha。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MomentumAcceleration5v20(BaseFactor):
    """短期5日收益与中期20日收益之差，衡量动量加速度。正值表示近期动量强于中期趋势（加速上涨），负值表示动量衰减或反转。捕捉趋势加速与衰竭的alpha。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_mom_accel_5_20",
            name="Momentum Acceleration 5v20",
            display_name="动量加速度5-20",
            description="短期5日收益与中期20日收益之差，衡量动量加速度。正值表示近期动量强于中期趋势（加速上涨），负值表示动量衰减或反转。捕捉趋势加速与衰竭的alpha。",
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
