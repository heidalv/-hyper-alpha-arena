"""AI因子: 低高波动率 | 置信:60% | 最近N根K线的最高最低价范围与平均真实波幅的比值。当范围缩小且ATR下降时，市场波动收窄，容易发生突发反向，类似于亏损模式中的止损与超时。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Low_High_Volatility(BaseFactor):
    """最近N根K线的最高最低价范围与平均真实波幅的比值。当范围缩小且ATR下降时，市场波动收窄，容易发生突发反向，类似于亏损模式中的止损与超时。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_lhv",
            name="Low High Volatility",
            display_name="低高波动率",
            description="最近N根K线的最高最低价范围与平均真实波幅的比值。当范围缩小且ATR下降时，市场波动收窄，容易发生突发反向，类似于亏损模式中的止损与超时。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        high = data['high']
        low = data['low']
        close = data['close']
        # 过去10天的最高最低范围（Range）
        period = 10
        recent_high = high.rolling(period).max()
        recent_low = low.rolling(period).min()
        range_val = recent_high - recent_low
        # ATR(14)
        tr = np.maximum(high - low, np.abs(high - close.shift(1)), np.abs(low - close.shift(1)))
        atr = tr.rolling(14).mean()
        # 比值，范围/ATR，低比值表示波动收窄
        ratio = range_val / (atr + 1e-10)
        # 归一化到[-1,1]，假设正常比值在1~5之间，低于1.5可能风险
        # 使用负向映射：比值越低越接近-1
        result = 1 - 2 * (1 / (1 + np.exp(-1.5 * (ratio - 2))))
        return result.fillna(0).clip(-1, 1)
