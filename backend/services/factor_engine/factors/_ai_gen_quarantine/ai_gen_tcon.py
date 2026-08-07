"""AI因子: 趋势一致性因子 | 置信:55% | 比较短期（10周期）和长期（50周期）移动平均线的方向是否一致，若不一致则给予负向信号，避免趋势不明朗时的持仓。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TrendConsistencyIndicator(BaseFactor):
    """比较短期（10周期）和长期（50周期）移动平均线的方向是否一致，若不一致则给予负向信号，避免趋势不明朗时的持仓。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_tcon",
            name="Trend Consistency Indicator",
            display_name="趋势一致性因子",
            description="比较短期（10周期）和长期（50周期）移动平均线的方向是否一致，若不一致则给予负向信号，避免趋势不明朗时的持仓。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close']
        ma_short = close.rolling(10).mean()
        ma_long = close.rolling(50).mean()
        # 计算方向：斜率（差分）的正负
        short_slope = ma_short.diff(1).fillna(0)
        long_slope = ma_long.diff(1).fillna(0)
        # 方向一致：两个斜率同号或其中之一为0
        same_direction = np.sign(short_slope) == np.sign(long_slope)
        factor = pd.Series(np.where(same_direction, 1.0, -1.0), index=data.index)
        return factor
