"""AI因子: 动量加速度 | 置信:60% | 短期动量与长期动量的差值反映动量加速度。当短期动量显著强于长期动量时，趋势加速上行；反之加速下行。捕捉趋势启动与衰竭的拐点。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MomentumAcceleration(BaseFactor):
    """短期动量与长期动量的差值反映动量加速度。当短期动量显著强于长期动量时，趋势加速上行；反之加速下行。捕捉趋势启动与衰竭的拐点。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_momentum_acceleration",
            name="Momentum Acceleration",
            display_name="动量加速度",
            description="短期动量与长期动量的差值反映动量加速度。当短期动量显著强于长期动量时，趋势加速上行；反之加速下行。捕捉趋势启动与衰竭的拐点。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        short_mom = data['close'].pct_change(5)
        long_mom = data['close'].pct_change(20)
        vol = data['close'].pct_change().rolling(20).std()
        result = ((short_mom - long_mom) / (vol + 1e-9)).rolling(3).mean().clip(-1, 1)
        return result
