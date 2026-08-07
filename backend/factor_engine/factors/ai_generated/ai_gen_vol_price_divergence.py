"""AI因子: 量价背离 | 置信:60% | 当价格下跌但成交量萎缩时，空头动能减弱，易引发反弹。计算价格变化与成交量变化的相关性符号。使用收盘价变化率与成交量变化率的差，经滚动标准化后映射到[-1,1]。正值表示量价背离（看涨信号），负值表示量价同步（趋势继续）。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class Volume-Price Divergence(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_vol_price_divergence", name="Volume-Price Divergence",
        display_name="量价背离", description="当价格下跌但成交量萎缩时，空头动能减弱，易引发反弹。计算价格变化与成交量变化的相关性符号。使用收盘价变化率与成交量变化率的差，经滚动标准化后映射到[-1,1]。正值表示量价背离（看涨信号），负值表示量价同步（趋势继续）。",
        category="technical", subcategory="volume",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    close = data['close']
    volume = data['volume']
    n = 10
    # 价格变化率
    price_ret = close.pct_change()
    # 成交量变化率
    vol_ret = volume.pct_change()
    # 滚动相关系数
    corr = price_ret.rolling(window=n).corr(vol_ret)
    # 直接使用相关系数的相反数作为背离信号
    raw = -corr
    # 标准化到[-1,1]，亦可直接使用
    result = raw.fillna(0).clip(-1, 1)
    return result
