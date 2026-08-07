"""AI因子: 假突破检测因子 | 置信:60% | 检测价格突破近期高点或低点时，成交量是否未能有效放大，从而识别假突破。当价格创20日新高但成交量低于20日均量时，视为假突破做空信号（-1）；当价格创20日新低但成交量低于均量时，视为假突破做多信号（+1）。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class Fake_Breakout_Detector(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_fakbrk", name="Fake_Breakout_Detector",
        display_name="假突破检测因子", description="检测价格突破近期高点或低点时，成交量是否未能有效放大，从而识别假突破。当价格创20日新高但成交量低于20日均量时，视为假突破做空信号（-1）；当价格创20日新低但成交量低于均量时，视为假突破做多信号（+1）。",
        category="behavioral", subcategory="contrarian",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    import numpy as np
    high = data['high']
    low = data['low']
    close = data['close']
    volume = data['volume']
    window = 20
    # 近期高低点
    recent_high = high.rolling(window).max()
    recent_low = low.rolling(window).min()
    vol_ma = volume.rolling(window).mean()
    # 突破条件
    close_high_break = close > recent_high.shift(1)
    close_low_break = close < recent_low.shift(1)
    vol_decrease = volume < vol_ma
    # 信号
    signal = np.where(close_high_break & vol_decrease, -1,
                      np.where(close_low_break & vol_decrease, 1, 0))
    return pd.Series(signal, index=data.index)
