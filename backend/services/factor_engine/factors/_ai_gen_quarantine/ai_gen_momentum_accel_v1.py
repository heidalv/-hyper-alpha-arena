"""AI因子: 多周期动量加速度 | 置信:60% | 用短周期收益减长周期收益衡量动量加速度：短期强于长期说明趋势正在加速，未来上涨概率更高；反之动量衰减预示回落。经波动率标准化后输出。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MultiPeriodMomentumAcceleration(BaseFactor):
    """用短周期收益减长周期收益衡量动量加速度：短期强于长期说明趋势正在加速，未来上涨概率更高；反之动量衰减预示回落。经波动率标准化后输出。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_momentum_accel_v1",
            name="Multi-period Momentum Acceleration",
            display_name="多周期动量加速度",
            description="用短周期收益减长周期收益衡量动量加速度：短期强于长期说明趋势正在加速，未来上涨概率更高；反之动量衰减预示回落。经波动率标准化后输出。",
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
