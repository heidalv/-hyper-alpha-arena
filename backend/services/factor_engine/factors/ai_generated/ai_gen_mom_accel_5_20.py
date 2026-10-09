"""AI因子: 动量加速度5-20 | 置信:60% | 短期动量减去长期动量，刻画动量加速度。正值表示近期涨速快于中期趋势，捕捉趋势加速延续；负值表示动量衰减。除以波动率归一化后截断到[-1,1]。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MomentumAcceleration5Vs20(BaseFactor):
    """短期动量减去长期动量，刻画动量加速度。正值表示近期涨速快于中期趋势，捕捉趋势加速延续；负值表示动量衰减。除以波动率归一化后截断到[-1,1]。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_mom_accel_5_20",
            name="Momentum Acceleration 5 vs 20",
            display_name="动量加速度5-20",
            description="短期动量减去长期动量，刻画动量加速度。正值表示近期涨速快于中期趋势，捕捉趋势加速延续；负值表示动量衰减。除以波动率归一化后截断到[-1,1]。",
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
