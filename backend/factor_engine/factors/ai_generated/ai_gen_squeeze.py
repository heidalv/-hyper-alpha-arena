"""AI因子: 布林带挤压突破 | 置信:50% | 基于布林带宽缩窄（挤压）后配合成交量爆发，识别趋势启动方向。当价格上破挤压区间且放量时做多，下破做空。可捕捉轧空行情，避免在窄幅震荡中交易。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class Bollinger Squeeze Breakout(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_squeeze", name="Bollinger Squeeze Breakout",
        display_name="布林带挤压突破", description="基于布林带宽缩窄（挤压）后配合成交量爆发，识别趋势启动方向。当价格上破挤压区间且放量时做多，下破做空。可捕捉轧空行情，避免在窄幅震荡中交易。",
        category="technical", subcategory="volatility",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    close = data['close']
    high = data['high']
    low = data['low']
    volume = data['volume']
    
    # 布林带参数
    period = 20
    ma = close.rolling(period).mean()
    std = close.rolling(period).std()
    upper = ma + 2 * std
    lower = ma - 2 * std
    bandwidth = (upper - lower) / ma
    # 挤压状态：带宽低于历史20%分位数
    threshold = bandwidth.rolling(50).quantile(0.2)
    squeeze = bandwidth < threshold
    
    # 价格突破方向
    up_break = (close > upper) & squeeze
    down_break = (close < lower) & squeeze
    # 成交量放大确认
    vol_ma = volume.rolling(20).mean()
    vol_surge = volume > vol_ma * 1.5
    
    # 信号：突破且放量得+/-
    signal = pd.Series(0, index=close.index)
    signal[up_break & vol_surge] = 1
    signal[down_break & vol_surge] = -1
    # 滚动平滑并映射到[-1,1]
    result = signal.rolling(3).mean().fillna(0)
    return result
