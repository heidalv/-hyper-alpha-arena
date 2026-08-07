"""AI因子: 趋势弱势因子 | 置信:70% | 通过价格偏离移动平均与ADX结合判断当前趋势强度，当趋势弱时提醒避免方向性持仓。使用14日ADX和价格相对60日均线偏离，ADX低于20且偏离度小时返回负值。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class Trend Weakness Factor(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_trend_weakness", name="Trend Weakness Factor",
        display_name="趋势弱势因子", description="通过价格偏离移动平均与ADX结合判断当前趋势强度，当趋势弱时提醒避免方向性持仓。使用14日ADX和价格相对60日均线偏离，ADX低于20且偏离度小时返回负值。",
        category="technical", subcategory="trend",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    import numpy as np
    close = data['close']
    high = data['high']
    low = data['low']
    # ADX
    tr = np.maximum(high - low, np.abs(high - close.shift(1)), np.abs(low - close.shift(1)))
    atr = tr.rolling(14).mean()
    plus_dm = np.where((high - high.shift(1)) > (low.shift(1) - low), high - high.shift(1), 0).clip(0)
    minus_dm = np.where((low.shift(1) - low) > (high - high.shift(1)), low.shift(1) - low, 0).clip(0)
    plus_di = 100 * plus_dm.rolling(14).mean() / atr
    minus_di = 100 * minus_dm.rolling(14).mean() / atr
    dx = 100 * np.abs(plus_di - minus_di) / (plus_di + minus_di + 1e-10)
    adx = dx.rolling(14).mean()
    # price deviation from 60ma
    ma60 = close.rolling(60).mean()
    deviation = (close - ma60) / (ma60 + 1e-10)
    # weak trend condition: adx < 20 and small deviation
    weak = (adx < 20) & (np.abs(deviation) < 0.05)
    result = np.where(weak, -1.0, 0.0)
    # add gradual influence based on adx magnitude
    result = np.where(adx < 25, result - 0.2 * (25 - adx) / 25, result)
    return pd.Series(result.clip(-1, 1), index=close.index)
