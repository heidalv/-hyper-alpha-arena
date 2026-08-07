"""AI因子: 价格路径震荡比 | 置信:60% | 计算日内（或N周期内）价格路径长度（高-低累积）与最终净变化（收盘-开盘）的比值，比值越大表示震荡越剧烈，越可能发生反转。因子值归一化到[-1,1]，高比值时输出负值（看跌），低比值时输出正值（看涨）。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class PricePathRatio(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_whipsaw", name="PricePathRatio",
        display_name="价格路径震荡比", description="计算日内（或N周期内）价格路径长度（高-低累积）与最终净变化（收盘-开盘）的比值，比值越大表示震荡越剧烈，越可能发生反转。因子值归一化到[-1,1]，高比值时输出负值（看跌），低比值时输出正值（看涨）。",
        category="composite", subcategory="mean_reversion",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    import numpy as np
    # 使用过去5根K线作为窗口
    window = 5
    # 路径长度：每个bar的高-低之和
    path_len = (data['high'] - data['low']).rolling(window, min_periods=2).sum()
    # 净变化：收盘-开盘
    net_change = data['close'] - data['open']
    net_change_abs = net_change.abs().rolling(window, min_periods=2).sum()
    # 避免除零
    ratio = np.where(net_change_abs > 0, path_len / (net_change_abs + 1e-10), 0)
    # 标准化到[-1,1]：ratio通常>1，取log后用sigmoid？简单用clip
    # 设定阈值为3，大于3则为强烈震荡，小于1则为趋势
    raw_signal = np.where(ratio > 3, -1, np.where(ratio < 1.5, 1, 0))
    return pd.Series(raw_signal, index=data.index).clip(-1, 1)
