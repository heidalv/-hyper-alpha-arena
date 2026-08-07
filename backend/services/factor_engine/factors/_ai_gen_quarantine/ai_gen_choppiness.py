"""AI因子: 混沌指数反转 | 置信:50% | 基于Choppiness Index衡量市场趋势清晰度。CI值高（市场混沌）时因子接近-1，CI值低（趋势明确）时因子接近+1。用于规避regime=unknown状态。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class ChoppinessIndexInverted(BaseFactor):
    """基于Choppiness Index衡量市场趋势清晰度。CI值高（市场混沌）时因子接近-1，CI值低（趋势明确）时因子接近+1。用于规避regime=unknown状态。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_choppiness",
            name="Choppiness Index (Inverted)",
            display_name="混沌指数反转",
            description="基于Choppiness Index衡量市场趋势清晰度。CI值高（市场混沌）时因子接近-1，CI值低（趋势明确）时因子接近+1。用于规避regime=unknown状态。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        n = 14
        high = data['high']
        low = data['low']
        close = data['close']
        atr = (high - low).rolling(n).mean()  # 简化ATR，不处理gap
        highest = high.rolling(n).max()
        lowest = low.rolling(n).min()
        range_sum = (high - low).rolling(n).sum()
        ci = 100 * np.log10(range_sum / (highest - lowest + 1e-10)) / np.log10(n)
        ci = ci.clip(0, 100)
        factor = 1 - 2 * (ci / 100)
        return factor
