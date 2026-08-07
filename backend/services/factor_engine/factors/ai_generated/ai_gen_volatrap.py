"""AI因子: 波动放大反转陷阱 | 置信:55% | 捕捉波动率突然放大后价格反向运动的模式。使用ATR衡量波动率变化，当ATR快速上升（超过近期均值1.5倍）且价格运动方向与前期趋势相反时，指示反转陷阱。返回[-1,1]，负值对应空头陷阱（价格上涨后快速下跌），正值对应多头陷阱。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityExpansionReversal(BaseFactor):
    """捕捉波动率突然放大后价格反向运动的模式。使用ATR衡量波动率变化，当ATR快速上升（超过近期均值1.5倍）且价格运动方向与前期趋势相反时，指示反转陷阱。返回[-1,1]，负值对应空头陷阱（价格上涨后快速下跌），正值对应多头陷阱。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volatrap",
            name="Volatility Expansion Reversal",
            display_name="波动放大反转陷阱",
            description="捕捉波动率突然放大后价格反向运动的模式。使用ATR衡量波动率变化，当ATR快速上升（超过近期均值1.5倍）且价格运动方向与前期趋势相反时，指示反转陷阱。返回[-1,1]，负值对应空头陷阱（价格上涨后快速下跌），正值对应多头陷阱。",
            category="composite",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        period = 14
        lookback = 20
        atr = (data['high'] - data['low']).rolling(period).mean()
        atr_change = atr / atr.shift(1) - 1.0
        atr_surge = atr_change > atr_change.rolling(lookback).mean() + atr_change.rolling(lookback).std()
        # 趋势方向：使用短期均线斜率
        ma_short = data['close'].rolling(5).mean()
        ma_long = data['close'].rolling(20).mean()
        uptrend = ma_short > ma_long
        downtrend = ma_short < ma_long
        # 反转条件：波动放大且价格反向
        reversal_down = atr_surge & uptrend & (data['close'].diff() < -data['close'].rolling(20).std()*0.5)
        reversal_up = atr_surge & downtrend & (data['close'].diff() > data['close'].rolling(20).std()*0.5)
        signal = pd.Series(0.0, index=data.index)
        signal[reversal_down] = -1.0
        signal[reversal_up] = 1.0
        return signal
