"""AI因子: 短期趋势反转 | 置信:60% | 捕捉连续上涨或下跌后的反转机会，类似持仓超时或AI反转亏损模式。统计连续N日收涨/收跌的天数，当连续天数达到阈值时发出反向信号。结合当日涨跌幅和开盘价位置增强信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class ShortTermTrendReversal(BaseFactor):
    """捕捉连续上涨或下跌后的反转机会，类似持仓超时或AI反转亏损模式。统计连续N日收涨/收跌的天数，当连续天数达到阈值时发出反向信号。结合当日涨跌幅和开盘价位置增强信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_str",
            name="Short-term Trend Reversal",
            display_name="短期趋势反转",
            description="捕捉连续上涨或下跌后的反转机会，类似持仓超时或AI反转亏损模式。统计连续N日收涨/收跌的天数，当连续天数达到阈值时发出反向信号。结合当日涨跌幅和开盘价位置增强信号。",
            category="technical",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        # 参数
        n = 5
        threshold = 3
        # 连续上涨/下跌天数
        up = (data['close'] > data['close'].shift(1)).astype(int)
        down = (data['close'] < data['close'].shift(1)).astype(int)
        streak_up = up * (up.groupby((up != up.shift()).cumsum()).cumcount() + 1)
        streak_down = down * (down.groupby((down != down.shift()).cumsum()).cumcount() + 1)
        # 信号
        signal = np.where(
            (streak_up >= threshold) & (data['close'] < data['open']),
            -1.0,
            0.0
        )
        signal = np.where(
            (streak_down >= threshold) & (data['close'] > data['open']),
            1.0,
            signal
        )
        # 平滑处理
        result = pd.Series(signal, index=data.index).rolling(3).mean().fillna(0)
        return result
