"""AI因子: 动量加速度比率 | 置信:60% | 短期动量与长期动量之差除以波动率，衡量动量加速度的标准化强度，正值表示近期动能强于中期，预示趋势延续。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MomentumAccelerationRatio(BaseFactor):
    """短期动量与长期动量之差除以波动率，衡量动量加速度的标准化强度，正值表示近期动能强于中期，预示趋势延续。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_mom_accel_ratio",
            name="Momentum Acceleration Ratio",
            display_name="动量加速度比率",
            description="短期动量与长期动量之差除以波动率，衡量动量加速度的标准化强度，正值表示近期动能强于中期，预示趋势延续。",
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
