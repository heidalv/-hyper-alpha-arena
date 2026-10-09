"""AI因子: 动量加速度背离 | 置信:60% | 用短周期收益与长周期收益之差衡量动量加速度，再除以波动率进行标准化，正值表示短期动量强于长期趋势，预测未来方向延续。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MomentumAccelerationDivergence(BaseFactor):
    """用短周期收益与长周期收益之差衡量动量加速度，再除以波动率进行标准化，正值表示短期动量强于长期趋势，预测未来方向延续。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_mom_accel_divergence",
            name="Momentum Acceleration Divergence",
            display_name="动量加速度背离",
            description="用短周期收益与长周期收益之差衡量动量加速度，再除以波动率进行标准化，正值表示短期动量强于长期趋势，预测未来方向延续。",
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
