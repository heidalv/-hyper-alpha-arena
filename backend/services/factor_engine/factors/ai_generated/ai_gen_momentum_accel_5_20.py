"""AI因子: 动量加速度(5-20) | 置信:60% | 短期5日收益与20日收益之差，衡量动量加速度。正值表示近期动能强于中期趋势，未来延续上涨概率更高；负值反之。属于经典动量类alpha。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MomentumAcceleration520(BaseFactor):
    """短期5日收益与20日收益之差，衡量动量加速度。正值表示近期动能强于中期趋势，未来延续上涨概率更高；负值反之。属于经典动量类alpha。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_momentum_accel_5_20",
            name="Momentum Acceleration 5-20",
            display_name="动量加速度(5-20)",
            description="短期5日收益与20日收益之差，衡量动量加速度。正值表示近期动能强于中期趋势，未来延续上涨概率更高；负值反之。属于经典动量类alpha。",
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
