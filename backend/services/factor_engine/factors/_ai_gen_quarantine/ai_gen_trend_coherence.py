"""AI因子: 趋势一致性指标 | 置信:60% | 比较短期和长期移动平均线的方向一致性与斜率差异。当短期MA方向与长期MA方向相反或斜率差距过大时，视为趋势不一致（未知状态），输出负值。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TrendCoherenceIndicator(BaseFactor):
    """比较短期和长期移动平均线的方向一致性与斜率差异。当短期MA方向与长期MA方向相反或斜率差距过大时，视为趋势不一致（未知状态），输出负值。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_trend_coherence",
            name="Trend Coherence Indicator",
            display_name="趋势一致性指标",
            description="比较短期和长期移动平均线的方向一致性与斜率差异。当短期MA方向与长期MA方向相反或斜率差距过大时，视为趋势不一致（未知状态），输出负值。",
            category="composite",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close']
        ma_short = close.rolling(window=20, min_periods=10).mean()
        ma_long = close.rolling(window=60, min_periods=30).mean()
        # 斜率用差分
        slope_short = ma_short.diff(5) / ma_short.shift(5).clip(lower=1e-8)
        slope_long = ma_long.diff(5) / ma_long.shift(5).clip(lower=1e-8)
        # 方向一致性: 符号相同为正，否则负
        sign_short = np.sign(slope_short)
        sign_long = np.sign(slope_long)
        same_sign = (sign_short == sign_long).astype(float)
        # 斜率差距
        slope_diff = (slope_short - slope_long).abs().clip(upper=0.1) / 0.1
        # 综合：方向一致且斜率接近得正分
        raw = same_sign * (1.0 - slope_diff)
        result = raw * 2.0 - 1.0  # 映射到[-1,1]
        return result.fillna(-1.0)
