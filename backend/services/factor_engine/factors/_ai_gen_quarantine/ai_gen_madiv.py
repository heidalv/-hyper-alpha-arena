"""AI因子: 均线背离 | 置信:60% | 检测价格创新高（近10日最高）但20日均线斜率下降，或价格创新低但均线斜率上升，预示趋势可能反转，给出对应方向信号。避免在bullish背离时做多。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MovingAverageDivergence(BaseFactor):
    """检测价格创新高（近10日最高）但20日均线斜率下降，或价格创新低但均线斜率上升，预示趋势可能反转，给出对应方向信号。避免在bullish背离时做多。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_madiv",
            name="Moving Average Divergence",
            display_name="均线背离",
            description="检测价格创新高（近10日最高）但20日均线斜率下降，或价格创新低但均线斜率上升，预示趋势可能反转，给出对应方向信号。避免在bullish背离时做多。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        close = data['close']
        ma20 = close.rolling(20).mean()
        # 均线斜率：20日均线变化率
        ma_slope = ma20.diff() / ma20.shift()
        # 最近10日最高价和最低价
        high10 = close.rolling(10).max()
        low10 = close.rolling(10).min()
        # 价格创新高（当前close是10日最高）且均线斜率下降（小于前一周期）
        new_high = (close == high10) & (high10 != high10.shift())
        slope_down = ma_slope < ma_slope.shift()
        bearish_div = new_high & slope_down  # 顶部背离，看跌
        # 价格创新低且均线斜率上升
        new_low = (close == low10) & (low10 != low10.shift())
        slope_up = ma_slope > ma_slope.shift()
        bullish_div = new_low & slope_up  # 底部背离，看涨
        # 信号：看跌-1，看涨+1，其他0
        raw = bullish_div.astype(float) * 1.0 - bearish_div.astype(float) * 1.0
        # 填充缺失并平滑
        return raw.fillna(0).clip(-1, 1)
