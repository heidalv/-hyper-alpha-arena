"""AI因子: 区间中位位置指标 | 置信:60% | 计算当前收盘价在过去N天最高最低区间内的相对位置。越接近0.5表示价格处于中间，趋势不明；越接近0或1表示处于极值可能反转。输出[-1,1]，1表示完全中位（无方向），-1表示极值（有方向）。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MidRangePositionIndicator(BaseFactor):
    """计算当前收盘价在过去N天最高最低区间内的相对位置。越接近0.5表示价格处于中间，趋势不明；越接近0或1表示处于极值可能反转。输出[-1,1]，1表示完全中位（无方向），-1表示极值（有方向）。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_range_position",
            name="Mid-Range Position Indicator",
            display_name="区间中位位置指标",
            description="计算当前收盘价在过去N天最高最低区间内的相对位置。越接近0.5表示价格处于中间，趋势不明；越接近0或1表示处于极值可能反转。输出[-1,1]，1表示完全中位（无方向），-1表示极值（有方向）。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        close = data['close']
        high = data['high'].rolling(window=20).max()
        low = data['low'].rolling(window=20).min()
        range_width = high - low
        # 避免除以零
        range_width = range_width.replace(0, np.nan)
        position = (close - low) / range_width  # 0~1
        # 映射：越接近0.5越接近1，越接近0或1越接近-1
        # 使用绝对值偏离0.5的程度：|position-0.5|*2，然后取反
        mid_deviation = abs(position - 0.5) * 2  # 0~1
        factor = 1 - 2 * mid_deviation  # 当偏离0时为1，偏离0.5时为-1
        factor = factor.clip(-1, 1)
        return factor.fillna(0)
