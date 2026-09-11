"""AI因子: 多周期动量加速度 | 置信:60% | 短周期动量减去长周期动量，衡量动量加速度：正值表示近期动能强于中期趋势，预示延续上涨；负值预示回落。经波动率标准化后输出。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MultiHorizonMomentumAcceleration(BaseFactor):
    """短周期动量减去长周期动量，衡量动量加速度：正值表示近期动能强于中期趋势，预示延续上涨；负值预示回落。经波动率标准化后输出。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_mom_accel_multi",
            name="Multi-Horizon Momentum Acceleration",
            display_name="多周期动量加速度",
            description="短周期动量减去长周期动量，衡量动量加速度：正值表示近期动能强于中期趋势，预示延续上涨；负值预示回落。经波动率标准化后输出。",
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
