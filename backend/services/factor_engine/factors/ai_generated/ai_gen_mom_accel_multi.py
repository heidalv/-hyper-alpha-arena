"""AI因子: 多周期动量加速度 | 置信:60% | 短期动量(5日)与中期动量(20日)之差，捕捉动量加速度。正值表示近期动能强于中期趋势，预示延续上涨；负值预示动能衰减。除以波动率标准化。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MultiHorizonMomentumAcceleration(BaseFactor):
    """短期动量(5日)与中期动量(20日)之差，捕捉动量加速度。正值表示近期动能强于中期趋势，预示延续上涨；负值预示动能衰减。除以波动率标准化。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_mom_accel_multi",
            name="Multi-Horizon Momentum Acceleration",
            display_name="多周期动量加速度",
            description="短期动量(5日)与中期动量(20日)之差，捕捉动量加速度。正值表示近期动能强于中期趋势，预示延续上涨；负值预示动能衰减。除以波动率标准化。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ret5 = data['close'].pct_change(5)
        ret20 = data['close'].pct_change(20)
        vol = data['close'].pct_change().rolling(20).std()
        result = ((ret5 - ret20) / (vol * 5 + 1e-9)).clip(-1, 1)
        return result
