"""AI因子: 时间衰减压力因子 | 置信:60% | 衡量价格在近期高位区域的停留时长，长时间高位盘整容易衰竭下跌。计算过去20周期内相对位置的EMA，高位持续给出负向信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TimeDecayPressure(BaseFactor):
    """衡量价格在近期高位区域的停留时长，长时间高位盘整容易衰竭下跌。计算过去20周期内相对位置的EMA，高位持续给出负向信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_timedecay",
            name="Time Decay Pressure",
            display_name="时间衰减压力因子",
            description="衡量价格在近期高位区域的停留时长，长时间高位盘整容易衰竭下跌。计算过去20周期内相对位置的EMA，高位持续给出负向信号。",
            category="behavioral",
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
        rolling_high = high.rolling(20).max()
        rolling_low = low.rolling(20).min()
        position = (close - rolling_low) / (rolling_high - rolling_low + 1e-9) - 0.5
        pos_ema = position.ewm(span=5, adjust=False).mean()
        result = (-np.tanh(pos_ema * 3)).fillna(0)
        return result
