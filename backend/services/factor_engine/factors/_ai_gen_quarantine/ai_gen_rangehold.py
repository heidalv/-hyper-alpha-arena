"""AI因子: 区间持仓时间 | 置信:60% | 量化价格在近期区间内横盘的时间比例，横盘越久（类似max_hold_timeout亏损），突破方向越不确定，做多风险增加。通过计算当前价格在最近N周期（如20）的区间中的位置，并结合连续横盘的天数。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class RangeHoldTime(BaseFactor):
    """量化价格在近期区间内横盘的时间比例，横盘越久（类似max_hold_timeout亏损），突破方向越不确定，做多风险增加。通过计算当前价格在最近N周期（如20）的区间中的位置，并结合连续横盘的天数。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_rangehold",
            name="Range Hold Time",
            display_name="区间持仓时间",
            description="量化价格在近期区间内横盘的时间比例，横盘越久（类似max_hold_timeout亏损），突破方向越不确定，做多风险增加。通过计算当前价格在最近N周期（如20）的区间中的位置，并结合连续横盘的天数。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        close = data['close']
        high = data['high']
        low = data['low']
        window = 20
        roll_high = high.rolling(window).max()
        roll_low = low.rolling(window).min()
        # 当前价格在区间内的百分位（0到1）
        range_width = roll_high - roll_low
        position = (close - roll_low) / range_width.replace(0, np.nan)
        # 判断是否处于中间50%（0.25~0.75）作为横盘区域
        in_middle = ((position >= 0.25) & (position <= 0.75)).astype(int)
        # 连续处于中间区域的天数（累加，重置）
        consecutive = in_middle * (in_middle.groupby((in_middle != in_middle.shift()).cumsum()).cumcount() + 1)
        # 归一化：连续天数越多，因子越负（看空）
        max_consecutive = 10  # 最多考虑10天
        result = - (consecutive / max_consecutive).clip(0, 1)
        # 额外惩罚：当价格处于极端位置但横盘很久？这里简化为仅考虑中间横盘
        return result.fillna(0)
