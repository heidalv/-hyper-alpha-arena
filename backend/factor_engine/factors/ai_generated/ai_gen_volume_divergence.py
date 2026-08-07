"""AI因子: 量价背离因子 | 置信:70% | 计算过去5周期价格变化与成交量变化的方向一致性。通过价格收益率和成交量变化率的相关系数（滚动5期）的负值来度量背离。当价格涨而成交量跌（负相关）时，因子接近-1，预示缺乏支撑可能反转；当量价齐升（正相关）时因子接近+1。值域[-1,1]。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class Volume-Price Divergence(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_volume_divergence", name="Volume-Price Divergence",
        display_name="量价背离因子", description="计算过去5周期价格变化与成交量变化的方向一致性。通过价格收益率和成交量变化率的相关系数（滚动5期）的负值来度量背离。当价格涨而成交量跌（负相关）时，因子接近-1，预示缺乏支撑可能反转；当量价齐升（正相关）时因子接近+1。值域[-1,1]。",
        category="behavioral", subcategory="volume",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    import numpy as np
    import pandas as pd
    close = data['close']
    volume = data['volume']
    ret = close.pct_change()
    vol_change = volume.pct_change()
    # 滚动5期相关系数
    def rolling_corr(x, y, window):
        return x.rolling(window).corr(y)
    corr = rolling_corr(ret, vol_change, 5)
    # 背离：负相关表示背离，取负映射到[-1,1]，同时处理NaN
    factor = -corr
    factor = factor.clip(-1, 1)
    return factor
