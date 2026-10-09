"""AI因子: 多周期动量加速度 | 置信:60% | 用短周期收益与长周期收益之差衡量动量加速度，再除以波动率做标准化。正值表示近期动能强于中期趋势，未来上涨概率更高；负值反之。属于动量类alpha。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MultiTimeframeMomentumAcceleration(BaseFactor):
    """用短周期收益与长周期收益之差衡量动量加速度，再除以波动率做标准化。正值表示近期动能强于中期趋势，未来上涨概率更高；负值反之。属于动量类alpha。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_mom_accel_multi_tf",
            name="Multi-Timeframe Momentum Acceleration",
            display_name="多周期动量加速度",
            description="用短周期收益与长周期收益之差衡量动量加速度，再除以波动率做标准化。正值表示近期动能强于中期趋势，未来上涨概率更高；负值反之。属于动量类alpha。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        short = data['close'].pct_change(5)
        long = data['close'].pct_change(20)
        vol = data['close'].pct_change().rolling(20).std()
        result = ((short - long) / (vol + 1e-9)).clip(-1, 1)
        return result
