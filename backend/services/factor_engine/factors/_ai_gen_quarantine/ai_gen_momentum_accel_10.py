"""AI因子: 动量加速度 | 置信:60% | 短期5日动量减去长期20日动量，衡量动量加速度；正值表示近期动能强于中期趋势，预示上涨延续概率高。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MomentumAcceleration10(BaseFactor):
    """短期5日动量减去长期20日动量，衡量动量加速度；正值表示近期动能强于中期趋势，预示上涨延续概率高。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_momentum_accel_10",
            name="Momentum Acceleration 10",
            display_name="动量加速度",
            description="短期5日动量减去长期20日动量，衡量动量加速度；正值表示近期动能强于中期趋势，预示上涨延续概率高。",
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
