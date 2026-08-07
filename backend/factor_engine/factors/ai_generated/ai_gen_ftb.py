"""AI因子: 假突破识别 | 置信:60% | 识别价格突破近期高点或低点但成交量未显著放大的假突破。计算过去20日最高价和最低价，当收盘价突破最高价时记为向上突破，突破最低价时向下突破；比较突破日成交量与过去20日平均成交量，若成交量低于平均成交量的1.2倍，则判定为假突破。向上假突破看跌（-1），向下假突破看涨（+1），否则为0。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class False_Breakout_Detection(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_ftb", name="False_Breakout_Detection",
        display_name="假突破识别", description="识别价格突破近期高点或低点但成交量未显著放大的假突破。计算过去20日最高价和最低价，当收盘价突破最高价时记为向上突破，突破最低价时向下突破；比较突破日成交量与过去20日平均成交量，若成交量低于平均成交量的1.2倍，则判定为假突破。向上假突破看跌（-1），向下假突破看涨（+1），否则为0。",
        category="behavioral", subcategory="volume",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    import numpy as np
    # 过去20日高低点
    high20 = data['high'].rolling(20).max()
    low20 = data['low'].rolling(20).min()
    # 成交量均值
    vol_mean = data['volume'].rolling(20).mean()
    # 突破信号
    up_break = data['close'] > high20.shift(1)
    down_break = data['close'] < low20.shift(1)
    # 成交量条件：小于均量的1.2倍
    low_vol = data['volume'] < vol_mean * 1.2
    # 假突破信号
    signal = np.zeros(len(data))
    signal[up_break & low_vol] = -1.0
    signal[down_break & low_vol] = 1.0
    return pd.Series(signal, index=data.index)
