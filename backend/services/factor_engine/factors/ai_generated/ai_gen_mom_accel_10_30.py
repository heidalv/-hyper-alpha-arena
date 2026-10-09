"""AI因子: 动量加速度差 | 置信:58% | 短期动量与长期动量之差衡量趋势加速度，正值表示近期动能强于中期趋势，预期延续上涨；负值预期下跌。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MomentumAccelerationSpread(BaseFactor):
    """短期动量与长期动量之差衡量趋势加速度，正值表示近期动能强于中期趋势，预期延续上涨；负值预期下跌。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_mom_accel_10_30",
            name="Momentum Acceleration Spread",
            display_name="动量加速度差",
            description="短期动量与长期动量之差衡量趋势加速度，正值表示近期动能强于中期趋势，预期延续上涨；负值预期下跌。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        short = data['close'].pct_change(10)
        long = data['close'].pct_change(30)
        vol = data['close'].pct_change().rolling(30).std()
        result = ((short - long) / (vol + 1e-9)).clip(-1, 1)
        return result
