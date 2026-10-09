"""AI因子: 动量加速度 | 置信:60% | 短期动量与中期动量之差，衡量趋势加速或衰减。正值表示近期动能强于中期，趋势加速上行；负值表示动能衰竭。捕捉动量延续与反转的切换点。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MomentumAcceleration10(BaseFactor):
    """短期动量与中期动量之差，衡量趋势加速或衰减。正值表示近期动能强于中期，趋势加速上行；负值表示动能衰竭。捕捉动量延续与反转的切换点。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_mom_accel_10",
            name="Momentum Acceleration 10",
            display_name="动量加速度",
            description="短期动量与中期动量之差，衡量趋势加速或衰减。正值表示近期动能强于中期，趋势加速上行；负值表示动能衰竭。捕捉动量延续与反转的切换点。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        fast = data['close'].pct_change(5)
        slow = data['close'].pct_change(20)
        vol = data['close'].pct_change().rolling(20).std() + 1e-9
        result = ((fast - slow) / vol).clip(-1, 1)
        return result
