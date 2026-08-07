"""AI因子: 时间陷阱指标 | 置信:60% | 衡量价格在一定窗口内位于高低点区间中间区域的时间比例。当市场长时间无法突破窄幅区间时，该指标为负（-1），警示可能触发max_hold_timeout。当价格突破区间边缘时，指标转正。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TimeTrapIndicator(BaseFactor):
    """衡量价格在一定窗口内位于高低点区间中间区域的时间比例。当市场长时间无法突破窄幅区间时，该指标为负（-1），警示可能触发max_hold_timeout。当价格突破区间边缘时，指标转正。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_tt",
            name="Time Trap Indicator",
            display_name="时间陷阱指标",
            description="衡量价格在一定窗口内位于高低点区间中间区域的时间比例。当市场长时间无法突破窄幅区间时，该指标为负（-1），警示可能触发max_hold_timeout。当价格突破区间边缘时，指标转正。",
            category="behavioral",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        import pandas as pd
        high = data['high']
        low = data['low']
        close = data['close']
        window = 20
        high_roll = high.rolling(window=window).max()
        low_roll = low.rolling(window=window).min()
        mid = (high_roll + low_roll) / 2
        half_range = (high_roll - low_roll) / 2
        # Avoid division by zero
        half_range = half_range.replace(0, np.nan)
        z_score = (close - mid) / half_range
        # Trap zone: when z_score in [-0.5, 0.5] for many bars in window
        in_trap = (z_score.abs() <= 0.5).rolling(window=window).mean()
        # Transform: 1 = no trap (price near edges), -1 = deep trap
        result = -2 * (in_trap - 0.5)
        result = result.clip(-1, 1)
        return result.fillna(0)
