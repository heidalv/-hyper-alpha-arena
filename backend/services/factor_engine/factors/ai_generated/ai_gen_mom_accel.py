"""AI因子: 动量加速度 | 置信:60% | 短周期收益减去长周期收益，衡量动量加速度：正值表示近期动能强于中期趋势，预示延续上涨；负值预示动能衰竭。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MomentumAcceleration(BaseFactor):
    """短周期收益减去长周期收益，衡量动量加速度：正值表示近期动能强于中期趋势，预示延续上涨；负值预示动能衰竭。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_mom_accel",
            name="Momentum Acceleration",
            display_name="动量加速度",
            description="短周期收益减去长周期收益，衡量动量加速度：正值表示近期动能强于中期趋势，预示延续上涨；负值预示动能衰竭。",
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
