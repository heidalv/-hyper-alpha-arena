"""AI因子: 趋势成交量一致性指标 | 置信:60% | 度量价格趋势方向与成交量变化的一致性。当价格创新高但成交量萎缩，或价格创新低但成交量放大时，表明趋势可能疲弱或反转，导致亏损。因子值接近+1表示趋势与成交量背离（危险），接近-1表示一致（安全）。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class Trend-Volume Consistency Indicator(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_trend_confirm", name="Trend-Volume Consistency Indicator",
        display_name="趋势成交量一致性指标", description="度量价格趋势方向与成交量变化的一致性。当价格创新高但成交量萎缩，或价格创新低但成交量放大时，表明趋势可能疲弱或反转，导致亏损。因子值接近+1表示趋势与成交量背离（危险），接近-1表示一致（安全）。",
        category="composite", subcategory="momentum",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    # data: DataFrame with columns ['open','high','low','close','volume']
    import pandas as pd
    import numpy as np
    close = data['close']
    volume = data['volume']
    
    # 计算价格变化方向
    price_change = close.pct_change(5)
    # 计算成交量变化方向
    vol_change = volume.pct_change(5)
    
    # 计算滚动相关性（例如20期）
    corr = price_change.rolling(20).corr(vol_change)
    
    # 转换为离散信号：强正相关 -> -1（一致），强负相关 -> +1（背离）
    # 使用tanh放大
    raw = -corr  # 正相关表示一致，取负使之成为背离信号
    result = np.tanh(raw * 5)  # 放大并限定在[-1,1]
    return result.fillna(0)
