"""AI因子: 趋势效率比 | 置信:60% | 衡量价格运动的趋势性，基于过去20个周期的净变化与路径总长度之比。值接近+1表示强劲上涨趋势，接近-1表示强劲下跌趋势，接近0表示震荡盘整。避免在低效率（震荡）市场中开仓。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class Efficiency Ratio(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_er", name="Efficiency Ratio",
        display_name="趋势效率比", description="衡量价格运动的趋势性，基于过去20个周期的净变化与路径总长度之比。值接近+1表示强劲上涨趋势，接近-1表示强劲下跌趋势，接近0表示震荡盘整。避免在低效率（震荡）市场中开仓。",
        category="technical", subcategory="trend",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    import pandas as pd
    import numpy as np
    close = data['close']
    n = 20
    net_change = close - close.shift(n)
    total_path = close.diff().abs().rolling(n).sum()
    er = np.abs(net_change) / total_path.replace(0, np.nan)
    er = er.fillna(0)
    direction = np.sign(net_change).fillna(0)
    factor = direction * er
    return factor.clip(-1, 1)
