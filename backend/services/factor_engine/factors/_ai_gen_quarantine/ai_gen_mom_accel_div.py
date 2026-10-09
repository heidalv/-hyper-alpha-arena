"""AI因子: 动量加速度背离 | 置信:60% | 短期动量与中期动量之差衡量加速度，再除以波动率做标准化，正值代表上涨加速，负值代表下跌加速。捕捉趋势启动与衰竭的 alpha。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MomentumAccelerationDivergence(BaseFactor):
    """短期动量与中期动量之差衡量加速度，再除以波动率做标准化，正值代表上涨加速，负值代表下跌加速。捕捉趋势启动与衰竭的 alpha。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_mom_accel_div",
            name="Momentum Acceleration Divergence",
            display_name="动量加速度背离",
            description="短期动量与中期动量之差衡量加速度，再除以波动率做标准化，正值代表上涨加速，负值代表下跌加速。捕捉趋势启动与衰竭的 alpha。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        short = data['close'].pct_change(5)
        mid = data['close'].pct_change(20)
        vol = data['close'].pct_change().rolling(20).std() + 1e-9
        result = ((short - mid) / vol).clip(-1, 1)
        return result
