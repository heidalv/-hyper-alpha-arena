"""AI因子: 趋势强度因子 | 置信:65% | 基于线性回归的R²与斜率方向，衡量最近N根K线的趋势强度与方向。R²高时趋势明确，斜率正为+1，负为-1，否则为0。用于过滤震荡行情，避免在无明显趋势时开仓。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TrendStrength(BaseFactor):
    """基于线性回归的R²与斜率方向，衡量最近N根K线的趋势强度与方向。R²高时趋势明确，斜率正为+1，负为-1，否则为0。用于过滤震荡行情，避免在无明显趋势时开仓。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_trend_str",
            name="Trend_Strength",
            display_name="趋势强度因子",
            description="基于线性回归的R²与斜率方向，衡量最近N根K线的趋势强度与方向。R²高时趋势明确，斜率正为+1，负为-1，否则为0。用于过滤震荡行情，避免在无明显趋势时开仓。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        n = 20
        close = data['close']
        x = np.arange(n)
        def trend_strength(series):
            if len(series) < n:
                return 0.0
            y = series.values
            slope, intercept = np.polyfit(x, y, 1)
            predicted = slope * x + intercept
            ss_res = np.sum((y - predicted) ** 2)
            ss_tot = np.sum((y - np.mean(y)) ** 2)
            r2 = 1 - ss_res / ss_tot if ss_tot != 0 else 0
            if r2 < 0.3:
                return 0.0
            sig = 1.0 if slope > 0 else -1.0
            return sig * min(r2, 1.0)
        result = close.rolling(window=n, min_periods=n).apply(trend_strength, raw=False)
        return result.fillna(0.0)
