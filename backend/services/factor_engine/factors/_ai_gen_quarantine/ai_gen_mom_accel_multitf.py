"""AI因子: 多周期动量加速度 | 置信:60% | 短期动量与中期动量的差值，衡量动量加速度。短期动量强于中期动量时为正，代表趋势正在加速，未来延续上涨概率更高；反之代表动量衰竭，未来回落概率更高。属于动量类经典因子。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MultiTimeframeMomentumAcceleration(BaseFactor):
    """短期动量与中期动量的差值，衡量动量加速度。短期动量强于中期动量时为正，代表趋势正在加速，未来延续上涨概率更高；反之代表动量衰竭，未来回落概率更高。属于动量类经典因子。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_mom_accel_multitf",
            name="Multi-Timeframe Momentum Acceleration",
            display_name="多周期动量加速度",
            description="短期动量与中期动量的差值，衡量动量加速度。短期动量强于中期动量时为正，代表趋势正在加速，未来延续上涨概率更高；反之代表动量衰竭，未来回落概率更高。属于动量类经典因子。",
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
