"""AI因子: 噪声水平指标 | 置信:55% | 通过比较价格连续变化的方向同向性来评估噪声比例。若大部分K线涨跌方向频繁反转（高噪声），则市场无序，容易出现tiny亏损和止损；反之低噪声时趋势稳健。因子值在[-1,1]之间。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class Noise level indicator(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_noise_level", name="Noise level indicator",
        display_name="噪声水平指标", description="通过比较价格连续变化的方向同向性来评估噪声比例。若大部分K线涨跌方向频繁反转（高噪声），则市场无序，容易出现tiny亏损和止损；反之低噪声时趋势稳健。因子值在[-1,1]之间。",
        category="behavioral", subcategory="mean_reversion",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    import pandas as pd
    import numpy as np
    close = data['close']
    ret = close.pct_change()
    # 符号序列
    sign = np.sign(ret)
    # 计算连续同向的比例（滚动窗口14）
    window = 14
    def noise_ratio(series):
        if len(series) < window:
            return np.nan
        # 相邻符号异向的次数
        diff = (series[:-1] != series[1:]).sum()
        # 噪声比例 = 反向次数 / (总变化数-1)
        return diff / (len(series) - 1)
    roll_sign = sign.rolling(window).apply(noise_ratio, raw=True)
    # 噪声比例0~1，映射到[-1,1]: 0.5为中性，低于0.5低噪声->正值，高于0.5高噪声->负值
    result = 1 - 2 * roll_sign
    result = result.clip(-1, 1)
    return result
