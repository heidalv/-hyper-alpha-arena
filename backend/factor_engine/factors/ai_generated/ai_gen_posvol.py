"""AI因子: 位置量能摆动指标 | 置信:65% | 基于价格在布林带中的相对位置与成交量异常，识别极端超买超卖区域。当价格位于布林带上轨上方且成交量显著萎缩时，认为短期回调风险大（-1）；当价格位于下轨下方且成交量放大时，认为超卖反弹概率高（+1）。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class Position_Volume_Oscillator(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_posvol", name="Position_Volume_Oscillator",
        display_name="位置量能摆动指标", description="基于价格在布林带中的相对位置与成交量异常，识别极端超买超卖区域。当价格位于布林带上轨上方且成交量显著萎缩时，认为短期回调风险大（-1）；当价格位于下轨下方且成交量放大时，认为超卖反弹概率高（+1）。",
        category="composite", subcategory="mean_reversion",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    import numpy as np
    close = data['close']
    volume = data['volume']
    # 布林带参数
    window = 20
    std_dev = 2
    sma = close.rolling(window).mean()
    std = close.rolling(window).std()
    upper = sma + std_dev * std
    lower = sma - std_dev * std
    # 价格位置 [0,1]
    pos = (close - lower) / (upper - lower)
    # 成交量变化率
    vol_ma = volume.rolling(window).mean()
    vol_ratio = volume / vol_ma
    # 信号构建
    # 超买: pos > 1 且 vol_ratio < 0.7 (缩量) -> -1
    # 超卖: pos < 0 且 vol_ratio > 1.5 (放量) -> +1
    # 其他情况线性映射到[-0.5,0.5]
    signal = np.where((pos > 1) & (vol_ratio < 0.7), -1,
                      np.where((pos < 0) & (vol_ratio > 1.5), 1,
                               (0.5 - pos) * 0.5))
    return pd.Series(signal, index=data.index)
