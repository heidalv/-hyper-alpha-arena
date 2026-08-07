"""AI因子: 趋势强度因子 | 置信:65% | 基于ADX和价格相对50周期均线位置。当ADX>25且价格在均线上方时看多(+1)，下方看空(-1)，否则输出0。避免在无趋势时开仓。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class Trend_Strength_ADX(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_trend_str", name="Trend_Strength_ADX",
        display_name="趋势强度因子", description="基于ADX和价格相对50周期均线位置。当ADX>25且价格在均线上方时看多(+1)，下方看空(-1)，否则输出0。避免在无趋势时开仓。",
        category="technical", subcategory="momentum",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    import numpy as np
    import pandas as pd
    high = data['high']
    low = data['low']
    close = data['close']
    # 计算ADX
    period = 14
    tr = np.maximum(high - low, np.maximum(abs(high - close.shift(1)), abs(low - close.shift(1))))
    atr = tr.rolling(period).mean()
    # Directional Movement
    up_move = high - high.shift(1)
    down_move = low.shift(1) - low
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0)
    plus_di = 100 * pd.Series(plus_dm).rolling(period).mean() / atr
    minus_di = 100 * pd.Series(minus_dm).rolling(period).mean() / atr
    dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di + 1e-10)
    adx = dx.rolling(period).mean()
    # 50日均线
    ma50 = close.rolling(50).mean()
    # 信号
    result = pd.Series(0.0, index=data.index)
    trend_mask = adx > 25
    bull_mask = (close > ma50) & trend_mask
    bear_mask = (close < ma50) & trend_mask
    result[bull_mask] = 1.0
    result[bear_mask] = -1.0
    return result
