"""AI因子: 趋势效率因子 | 置信:60% | 衡量价格净变化与总波动幅度的比率，反映方向性效率。当效率接近零时，市场震荡无方向，容易导致持仓超时亏损，因子值接近0或负；高效率则趋势明确，因子值接近+1/-1。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TrendEfficiency(BaseFactor):
    """衡量价格净变化与总波动幅度的比率，反映方向性效率。当效率接近零时，市场震荡无方向，容易导致持仓超时亏损，因子值接近0或负；高效率则趋势明确，因子值接近+1/-1。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_treff",
            name="Trend Efficiency",
            display_name="趋势效率因子",
            description="衡量价格净变化与总波动幅度的比率，反映方向性效率。当效率接近零时，市场震荡无方向，容易导致持仓超时亏损，因子值接近0或负；高效率则趋势明确，因子值接近+1/-1。",
            category="composite",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        period = 20
        close = data['close']
        high = data['high']
        low = data['low']
        delta = close - close.shift(period)
        range_ = high.rolling(period).max() - low.rolling(period).min()
        efficiency = delta / (range_ + 1e-9)
        result = efficiency.clip(-1, 1)
        return result
