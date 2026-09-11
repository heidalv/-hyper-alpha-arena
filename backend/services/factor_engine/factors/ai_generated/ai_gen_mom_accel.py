"""AI因子: 多周期动量加速度 | 置信:58% | 短期动量(5日)减去长期动量(20日)衡量动量加速度，正值表示近期动能强于中期趋势，预示延续上涨；负值表示动能衰减。用波动率归一化后截断，捕捉趋势加速与反转拐点。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MultiHorizonMomentumAcceleration(BaseFactor):
    """短期动量(5日)减去长期动量(20日)衡量动量加速度，正值表示近期动能强于中期趋势，预示延续上涨；负值表示动能衰减。用波动率归一化后截断，捕捉趋势加速与反转拐点。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_mom_accel",
            name="Multi-Horizon Momentum Acceleration",
            display_name="多周期动量加速度",
            description="短期动量(5日)减去长期动量(20日)衡量动量加速度，正值表示近期动能强于中期趋势，预示延续上涨；负值表示动能衰减。用波动率归一化后截断，捕捉趋势加速与反转拐点。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        mom_s = data['close'].pct_change(5)
        mom_l = data['close'].pct_change(20)
        vol = data['close'].pct_change().rolling(20).std() + 1e-9
        result = ((mom_s - mom_l) / vol).clip(-1, 1)
        return result
