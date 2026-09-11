"""AI因子: 波动率突变突破 | 置信:55% | 已实现波动率相对长期均值的突变，配合收盘位置(close相对高低区间的位置)判断突破方向。高波动+收盘位置高=向上突破确认，高波动+收盘位置低=向下破位。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityRegimeBreakout(BaseFactor):
    """已实现波动率相对长期均值的突变，配合收盘位置(close相对高低区间的位置)判断突破方向。高波动+收盘位置高=向上突破确认，高波动+收盘位置低=向下破位。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volatility_breakout",
            name="Volatility Regime Breakout",
            display_name="波动率突变突破",
            description="已实现波动率相对长期均值的突变，配合收盘位置(close相对高低区间的位置)判断突破方向。高波动+收盘位置高=向上突破确认，高波动+收盘位置低=向下破位。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ret = data['close'].pct_change()
        vol_short = ret.rolling(5).std()
        vol_long = ret.rolling(30).std() + 1e-9
        vol_ratio = vol_short / vol_long
        close_pos = (data['close'] - data['low'].rolling(10).min()) / (data['high'].rolling(10).max() - data['low'].rolling(10).min() + 1e-9)
        result = ((vol_ratio - 1) * (close_pos - 0.5) * 2).clip(-1, 1)
        return result
