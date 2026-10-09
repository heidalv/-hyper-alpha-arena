"""AI因子: 多周期动量加速度 | 置信:60% | 短期收益率与长期收益率之差衡量动量加速度，除以波动率做标准化，正值表示近期动能加速向上，预测未来收益方向。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MultiPeriodMomentumAcceleration(BaseFactor):
    """短期收益率与长期收益率之差衡量动量加速度，除以波动率做标准化，正值表示近期动能加速向上，预测未来收益方向。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_mom_accel_multi",
            name="Multi-Period Momentum Acceleration",
            display_name="多周期动量加速度",
            description="短期收益率与长期收益率之差衡量动量加速度，除以波动率做标准化，正值表示近期动能加速向上，预测未来收益方向。",
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
