"""AI因子: 动量加速度(5日减20日) | 置信:60% | 短期动量与中期动量之差衡量动量加速度。当5日收益显著高于20日收益时，趋势正在加速，未来延续上涨概率高；反之加速下跌。用20日波动率标准化后截断到[-1,1]，作为趋势延续型alpha。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MomentumAcceleration5Over20(BaseFactor):
    """短期动量与中期动量之差衡量动量加速度。当5日收益显著高于20日收益时，趋势正在加速，未来延续上涨概率高；反之加速下跌。用20日波动率标准化后截断到[-1,1]，作为趋势延续型alpha。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_momentum_acceleration_5_20",
            name="Momentum Acceleration 5 over 20",
            display_name="动量加速度(5日减20日)",
            description="短期动量与中期动量之差衡量动量加速度。当5日收益显著高于20日收益时，趋势正在加速，未来延续上涨概率高；反之加速下跌。用20日波动率标准化后截断到[-1,1]，作为趋势延续型alpha。",
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
