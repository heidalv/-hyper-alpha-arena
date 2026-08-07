"""AI因子: 趋势疲劳 | 置信:60% | 计算短期动量与长期动量的差异，衡量趋势的衰竭程度。当短期动量明显弱于长期动量时，趋势可能即将反转或进入震荡，容易导致持仓超时亏损。值越负，趋势越疲弱。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TrendFatigueIndicator(BaseFactor):
    """计算短期动量与长期动量的差异，衡量趋势的衰竭程度。当短期动量明显弱于长期动量时，趋势可能即将反转或进入震荡，容易导致持仓超时亏损。值越负，趋势越疲弱。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_tfatigue",
            name="Trend Fatigue Indicator",
            display_name="趋势疲劳",
            description="计算短期动量与长期动量的差异，衡量趋势的衰竭程度。当短期动量明显弱于长期动量时，趋势可能即将反转或进入震荡，容易导致持仓超时亏损。值越负，趋势越疲弱。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        mom_short = close.pct_change(5)
        mom_long = close.pct_change(20)
        fatigue = mom_short - mom_long
        rolling_mean = fatigue.rolling(100, min_periods=20).mean()
        rolling_std = fatigue.rolling(100, min_periods=20).std()
        zscore = (fatigue - rolling_mean) / rolling_std
        result = zscore.clip(-3, 3) / 3.0
        return result.fillna(0)
