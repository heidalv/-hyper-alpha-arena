"""AI因子: 波动率调整动量加速度 | 置信:60% | 用短周期收益减长周期收益构造动量加速度，再除以已实现波动率进行风险调整，衡量单位波动下的趋势加速强度，正值代表上涨加速，负值代表下跌加速，输出[-1,1]。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityScaledMomentumAcceleration(BaseFactor):
    """用短周期收益减长周期收益构造动量加速度，再除以已实现波动率进行风险调整，衡量单位波动下的趋势加速强度，正值代表上涨加速，负值代表下跌加速，输出[-1,1]。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_scaled_mom_accel",
            name="Volatility Scaled Momentum Acceleration",
            display_name="波动率调整动量加速度",
            description="用短周期收益减长周期收益构造动量加速度，再除以已实现波动率进行风险调整，衡量单位波动下的趋势加速强度，正值代表上涨加速，负值代表下跌加速，输出[-1,1]。",
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
