"""AI因子: 趋势一致性因子 | 置信:65% | 比较短期均线（5日）与长期均线（30日）的斜率方向是否一致，同时考虑价格与均线的偏离。当趋势不一致时（如短期向上但长期向下），做多风险高。输出[-1,1]，负值表示趋势矛盾，不宜做多。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Trend_Consistency(BaseFactor):
    """比较短期均线（5日）与长期均线（30日）的斜率方向是否一致，同时考虑价格与均线的偏离。当趋势不一致时（如短期向上但长期向下），做多风险高。输出[-1,1]，负值表示趋势矛盾，不宜做多。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_trendcon",
            name="Trend Consistency",
            display_name="趋势一致性因子",
            description="比较短期均线（5日）与长期均线（30日）的斜率方向是否一致，同时考虑价格与均线的偏离。当趋势不一致时（如短期向上但长期向下），做多风险高。输出[-1,1]，负值表示趋势矛盾，不宜做多。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        short_ma = close.rolling(5).mean()
        long_ma = close.rolling(30).mean()
        short_slope = short_ma.diff(3) / short_ma.shift(3)
        long_slope = long_ma.diff(10) / long_ma.shift(10)
        alignment = np.sign(short_slope) * np.sign(long_slope)
        deviation = (close - long_ma) / long_ma
        raw = alignment * (1 - np.abs(deviation))  # 趋势一致且偏离不大时为正
        norm = (raw - raw.rolling(50).mean()) / raw.rolling(50).std()
        result = norm.clip(-3, 3) / 3
        return result.fillna(0)
