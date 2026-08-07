"""AI因子: 成交量激增趋势确认 | 置信:65% | 当成交量突然放大（超过过去N日均值一定倍数）且价格处于上升趋势时，趋势延续概率高，做空风险大。因子计算成交量比率与价格方向信号乘积，再归一化到[-1,1]。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class Volume Surge with Price Trend(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_volsurge", name="Volume Surge with Price Trend",
        display_name="成交量激增趋势确认", description="当成交量突然放大（超过过去N日均值一定倍数）且价格处于上升趋势时，趋势延续概率高，做空风险大。因子计算成交量比率与价格方向信号乘积，再归一化到[-1,1]。",
        category="behavioral", subcategory="volume",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    import pandas as pd
    import numpy as np
    n = 20
    vol_ma = data['volume'].rolling(n).mean()
    vol_ratio = data['volume'] / (vol_ma + 1e-10)
    # 价格趋势：短期均线斜率
    sma_short = data['close'].rolling(5).mean()
    sma_long = data['close'].rolling(20).mean()
    price_trend = (sma_short - sma_long) / (sma_long + 1e-10)
    # 成交量激增阈值
    surge = (vol_ratio > 1.5).astype(float)
    # 结合
    raw = surge * price_trend
    # 归一化
    result = np.tanh(raw * 5)  # 放大信号
    return result.fillna(0)
