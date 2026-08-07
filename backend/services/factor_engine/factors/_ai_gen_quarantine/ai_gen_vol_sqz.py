"""AI因子: 波动率挤压风险 | 置信:60% | 布林带宽度处于历史低位后突然扩张，预示波动爆发，易触发止损或持仓超时亏损。结合价格突破方向，提供方向性风险信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilitySqueeze(BaseFactor):
    """布林带宽度处于历史低位后突然扩张，预示波动爆发，易触发止损或持仓超时亏损。结合价格突破方向，提供方向性风险信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_sqz",
            name="Volatility Squeeze",
            display_name="波动率挤压风险",
            description="布林带宽度处于历史低位后突然扩张，预示波动爆发，易触发止损或持仓超时亏损。结合价格突破方向，提供方向性风险信号。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        close = data['close']
        high = data['high']
        low = data['low']
        bb_period = 20
        bb_std = 2
        squeeze_period = 120
        ma = close.rolling(bb_period).mean()
        std = close.rolling(bb_period).std()
        upper = ma + bb_std * std
        lower = ma - bb_std * std
        bb_width = (upper - lower) / ma
        width_rank = bb_width.rolling(squeeze_period).apply(lambda x: (x.iloc[-1] < x.iloc[:-1]).mean())
        break_dir = np.sign(close - ma.shift(1))
        squeeze_signal = (width_rank < 0.1).astype(float) * break_dir
        result = squeeze_signal.clip(-1, 1)
        return result
