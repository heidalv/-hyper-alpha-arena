"""AI因子: 动量加速度差 | 置信:55% | 短期动量(5日)与中期动量(20日)之差衡量动量加速度。正值表示近期动能强于中期趋势，趋势延续概率高；负值表示动能衰减，可能反转。捕捉趋势加速与衰竭的alpha。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MomentumAccelerationSpread(BaseFactor):
    """短期动量(5日)与中期动量(20日)之差衡量动量加速度。正值表示近期动能强于中期趋势，趋势延续概率高；负值表示动能衰减，可能反转。捕捉趋势加速与衰竭的alpha。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_mom_accel",
            name="Momentum Acceleration Spread",
            display_name="动量加速度差",
            description="短期动量(5日)与中期动量(20日)之差衡量动量加速度。正值表示近期动能强于中期趋势，趋势延续概率高；负值表示动能衰减，可能反转。捕捉趋势加速与衰竭的alpha。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        mom_s = data['close'].pct_change(5)
        mom_m = data['close'].pct_change(20)
        vol = data['close'].pct_change().rolling(20).std() + 1e-9
        result = ((mom_s - mom_m) / vol).clip(-1, 1)
        return result
