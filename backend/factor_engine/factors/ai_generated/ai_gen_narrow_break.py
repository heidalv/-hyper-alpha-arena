"""AI因子: 窄幅假突破因子 | 置信:60% | 检测在低波动率窄幅震荡后出现的假突破信号。当价格创出近期新高但收盘接近当日最低，且波动率处于低位时，判断为假突破并发出看空信号（-1）；反之，若创出新低但收盘接近最高，发出看多信号（+1）。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class Narrow Range Fakeout Factor(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_narrow_break", name="Narrow Range Fakeout Factor",
        display_name="窄幅假突破因子", description="检测在低波动率窄幅震荡后出现的假突破信号。当价格创出近期新高但收盘接近当日最低，且波动率处于低位时，判断为假突破并发出看空信号（-1）；反之，若创出新低但收盘接近最高，发出看多信号（+1）。",
        category="technical", subcategory="mean_reversion",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    import pandas as pd
    import numpy as np
    # 计算过去10日的价格范围（高-低）与过去20日均值比较
    high = data['high']
    low = data['low']
    close = data['close']
    range_10 = high.rolling(10).max() - low.rolling(10).min()
    avg_range_20 = (high - low).rolling(20).mean()
    # 窄幅震荡条件：最近10日范围小于过去20日平均范围的一半
    narrow = range_10 < (avg_range_20 * 0.5)
    # 突破条件：今日最高价创过去10日新高
    new_high = high == high.rolling(10).max()
    # 收盘位置：收盘价在当日最高最低区间内的相对位置
    close_position = (close - low) / (high - low).replace(0, np.nan)
    # 假突破信号：窄幅震荡且创出新高但收盘靠近低端（位置<0.3）
    fake_high = narrow & new_high & (close_position < 0.3)
    # 对称的假突破看多信号：创10日新低但收盘靠近高端
    new_low = low == low.rolling(10).min()
    fake_low = narrow & new_low & (close_position > 0.7)
    # 输出信号：看空-1，看多+1，否则0
    signal = pd.Series(0, index=data.index)
    signal[fake_high] = -1.0
    signal[fake_low] = 1.0
    return signal
