"""AI因子: 止损清扫反转因子 | 置信:60% | 识别价格快速突破近期关键支撑/阻力位后迅速回撤的止损清扫行为。通过比较当前收盘价与过去N根K线的均线偏离度，以及随后K线的反向运动强度，捕捉假突破后的反转机会。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class StopLossCleaningReversal(BaseFactor):
    """识别价格快速突破近期关键支撑/阻力位后迅速回撤的止损清扫行为。通过比较当前收盘价与过去N根K线的均线偏离度，以及随后K线的反向运动强度，捕捉假突破后的反转机会。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_stopclean",
            name="Stop Loss Cleaning Reversal",
            display_name="止损清扫反转因子",
            description="识别价格快速突破近期关键支撑/阻力位后迅速回撤的止损清扫行为。通过比较当前收盘价与过去N根K线的均线偏离度，以及随后K线的反向运动强度，捕捉假突破后的反转机会。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        n = 10  # 均线周期
        lookback = 3  # 回看突破后的反向K线数
        close = data['close']
        high = data['high']
        low = data['low']
        # 计算均线
        ma = close.rolling(n).mean()
        # 相对偏离度
        dev = (close - ma) / (ma + 1e-10)
        # 判断是否突破：当前价格超过均线2%以上
        breakout_up = dev > 0.02
        breakout_down = dev < -0.02
        # 后lookback根K线的最低价和最高价
        future_low = low.shift(-1).rolling(lookback).min()
        future_high = high.shift(-1).rolling(lookback).max()
        # 反转条件：向上突破后，后续最低价回落到均线附近（跌破均线）即反转看跌
        reversal_down = breakout_up & (future_low < ma)
        # 向下突破后，后续最高价回升到均线附近（越过均线）即反转看涨
        reversal_up = breakout_down & (future_high > ma)
        factor = pd.Series(0.0, index=data.index)
        factor[reversal_up] = 1.0
        factor[reversal_down] = -1.0
        return factor
