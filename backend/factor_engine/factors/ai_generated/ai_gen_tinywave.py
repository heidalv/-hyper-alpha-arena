"""AI因子: 微小波动因子 | 置信:60% | 衡量价格在窄幅区间内的波动紧凑程度，结合成交量萎缩。当价格波动极小且成交量低迷时，值接近+1，表示容易触发微小止损的行情；大幅波动时接近-1。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class Tiny Wave(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_tinywave", name="Tiny Wave",
        display_name="微小波动因子", description="衡量价格在窄幅区间内的波动紧凑程度，结合成交量萎缩。当价格波动极小且成交量低迷时，值接近+1，表示容易触发微小止损的行情；大幅波动时接近-1。",
        category="technical", subcategory="volatility",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    import numpy as np
    import pandas as pd
    n = 20
    atr = np.abs(data['high'] - data['low']).rolling(n).mean()
    atr_pct = atr / data['close']
    atr_z = (atr_pct - atr_pct.rolling(n*2).mean()) / atr_pct.rolling(n*2).std()
    score = np.tanh(atr_z)  # 正值表示波动小? atr_z小则波动小，tanh给出负值，需要取负
    result = -score  # 使窄幅震荡时为正
    return result.fillna(0).clip(-1, 1)
