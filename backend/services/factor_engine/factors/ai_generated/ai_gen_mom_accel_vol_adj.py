"""AI因子: 动量加速度(波动调整) | 置信:60% | 短期动量(5日)减去中期动量(20日)得到动量加速度，再用已实现波动率标准化，捕捉趋势加速或衰竭，预测未来收益方向。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MomentumAccelerationVolatilityAdjusted(BaseFactor):
    """短期动量(5日)减去中期动量(20日)得到动量加速度，再用已实现波动率标准化，捕捉趋势加速或衰竭，预测未来收益方向。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_mom_accel_vol_adj",
            name="Momentum Acceleration Volatility Adjusted",
            display_name="动量加速度(波动调整)",
            description="短期动量(5日)减去中期动量(20日)得到动量加速度，再用已实现波动率标准化，捕捉趋势加速或衰竭，预测未来收益方向。",
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
