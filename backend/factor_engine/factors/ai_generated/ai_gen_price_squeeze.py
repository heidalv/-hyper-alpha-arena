"""AI因子: 价格压缩因子 | 置信:60% | 基于布林带带宽（带宽=2*std/中轨）相对于过去20期带宽的百分位。当带宽处于近20期最低20%分位时，表示价格极度压缩，后续容易出现假突破或逆转，因子输出接近-1；反之带宽扩张时因子接近+1。值域[-1,1]。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class Price Squeeze(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_price_squeeze", name="Price Squeeze",
        display_name="价格压缩因子", description="基于布林带带宽（带宽=2*std/中轨）相对于过去20期带宽的百分位。当带宽处于近20期最低20%分位时，表示价格极度压缩，后续容易出现假突破或逆转，因子输出接近-1；反之带宽扩张时因子接近+1。值域[-1,1]。",
        category="technical", subcategory="volatility",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    import numpy as np
    import pandas as pd
    close = data['close']
    high = data['high']
    low = data['low']
    # 使用典型价格计算布林带
    tp = (high + low + close) / 3
    sma = tp.rolling(20).mean()
    std = tp.rolling(20).std()
    bandwidth = (2 * std) / (sma + 1e-10)  # 相对带宽
    # 滚动百分位：当前带宽在过去20期内的分位数
    def rank_pct(series):
        return series.rank(pct=True).iloc[-1]
    pct = bandwidth.rolling(20).apply(rank_pct, raw=False)
    # 映射：pct从0到1映射到-1到1
    factor = 2 * pct - 1
    return factor
