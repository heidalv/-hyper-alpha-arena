"""AI因子: 价格区间位置 | 置信:60% | 计算当前收盘价在过去20日最高最低区间内的相对位置，通过4*|position-0.5|-1映射到[-1,1]。当价格位于区间中央(regime unknown)时输出-1，位于两端时输出+1，表示趋势明确(正值适合做多)。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Price_Range_Position(BaseFactor):
    """计算当前收盘价在过去20日最高最低区间内的相对位置，通过4*|position-0.5|-1映射到[-1,1]。当价格位于区间中央(regime unknown)时输出-1，位于两端时输出+1，表示趋势明确(正值适合做多)。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_prp",
            name="Price Range Position",
            display_name="价格区间位置",
            description="计算当前收盘价在过去20日最高最低区间内的相对位置，通过4*|position-0.5|-1映射到[-1,1]。当价格位于区间中央(regime unknown)时输出-1，位于两端时输出+1，表示趋势明确(正值适合做多)。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        high_20 = data['high'].rolling(20).max()
        low_20 = data['low'].rolling(20).min()
        position = (close - low_20) / (high_20 - low_20 + 1e-8)
        result = 4.0 * (position - 0.5).abs() - 1.0
        result = result.fillna(0)
        return result
