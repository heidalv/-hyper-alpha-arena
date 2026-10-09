"""AI因子: 长上影线拒绝回落 | 置信:55% | 长上影线代表上方抛压，收盘位置偏低时后续易回落。用上影线相对振幅的占比刻画卖方拒绝强度，取负向作为未来收益方向预测。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class UpperShadowRejection(BaseFactor):
    """长上影线代表上方抛压，收盘位置偏低时后续易回落。用上影线相对振幅的占比刻画卖方拒绝强度，取负向作为未来收益方向预测。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_upper_shadow_reject",
            name="Upper Shadow Rejection",
            display_name="长上影线拒绝回落",
            description="长上影线代表上方抛压，收盘位置偏低时后续易回落。用上影线相对振幅的占比刻画卖方拒绝强度，取负向作为未来收益方向预测。",
            category="technical",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        rng = (data['high'] - data['low']) + 1e-9
        upper = data['high'] - data[['open','close']].max(axis=1)
        ratio = upper / rng
        result = (-ratio).rolling(5).mean().clip(-1, 1)
        return result
