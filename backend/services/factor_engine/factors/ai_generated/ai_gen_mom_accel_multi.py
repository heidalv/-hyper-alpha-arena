"""AI因子: 多周期动量加速度 | 置信:60% | 短期动量(5日)减去中期动量(20日)，衡量动量加速度。正值代表近期动能强于中期趋势，未来上涨概率更高；负值反之。用波动率归一化后clip。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MultiPeriodMomentumAcceleration(BaseFactor):
    """短期动量(5日)减去中期动量(20日)，衡量动量加速度。正值代表近期动能强于中期趋势，未来上涨概率更高；负值反之。用波动率归一化后clip。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_mom_accel_multi",
            name="Multi-period Momentum Acceleration",
            display_name="多周期动量加速度",
            description="短期动量(5日)减去中期动量(20日)，衡量动量加速度。正值代表近期动能强于中期趋势，未来上涨概率更高；负值反之。用波动率归一化后clip。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        fast = data['close'].pct_change(5)
        slow = data['close'].pct_change(20)
        vol = data['close'].pct_change().rolling(20).std()
        result = ((fast - slow) / (vol + 1e-9)).clip(-1, 1)
        return result
