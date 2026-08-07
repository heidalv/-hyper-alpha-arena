"""AI因子: 趋势质量评分 | 置信:60% | 基于ADX和移动平均线斜率评估趋势的可靠性。当ADX低于25且价格与短期均线偏离较小时，趋势质量低，因子为负；趋势强劲时为正。用于避免在无趋势或弱趋势中交易。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Trend_Quality_Score(BaseFactor):
    """基于ADX和移动平均线斜率评估趋势的可靠性。当ADX低于25且价格与短期均线偏离较小时，趋势质量低，因子为负；趋势强劲时为正。用于避免在无趋势或弱趋势中交易。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_adx_quality",
            name="Trend Quality Score",
            display_name="趋势质量评分",
            description="基于ADX和移动平均线斜率评估趋势的可靠性。当ADX低于25且价格与短期均线偏离较小时，趋势质量低，因子为负；趋势强劲时为正。用于避免在无趋势或弱趋势中交易。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        high, low, close = data['high'], data['low'], data['close']
        # ADX calculation (same as before)
        plus_dm = np.where((high - high.shift(1)) > (low.shift(1) - low), np.maximum(high - high.shift(1), 0), 0)
        minus_dm = np.where((low.shift(1) - low) > (high - high.shift(1)), np.maximum(low.shift(1) - low, 0), 0)
        tr = np.maximum(high - low, np.abs(high - close.shift(1)), np.abs(low - close.shift(1)))
        tr14 = tr.rolling(14).sum()
        plus_di14 = 100 * (plus_dm.rolling(14).sum() / tr14.replace(0, np.nan))
        minus_di14 = 100 * (minus_dm.rolling(14).sum() / tr14.replace(0, np.nan))
        dx = 100 * np.abs(plus_di14 - minus_di14) / (plus_di14 + minus_di14).replace(0, np.nan)
        adx = dx.rolling(14).mean()
        # Slope of 20-day MA
        ma20 = close.rolling(20).mean()
        slope = (ma20 - ma20.shift(5)) / ma20.shift(5) * 100
        # Combine: low ADX + flat slope -> low quality
        score = (adx / 50.0 - 0.5) + (np.sign(slope) * np.minimum(np.abs(slope) / 1.0, 1.0) * 0.5)
        result = np.clip(score, -1, 1)
        return result
