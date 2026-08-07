"""AI因子: 波动率稳定性指数 | 置信:65% | 通过短期ATR与长期ATR的比值衡量波动率是否处于异常稳定的状态。比值低于阈值时市场缺乏波动，容易触发微小平仓亏损 (master_running_close_tiny)，因子向-1移动；比值高于阈值时市场波动正常或剧烈，因子向+1移动。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class Volatility stability index(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_vol_stability", name="Volatility stability index",
        display_name="波动率稳定性指数", description="通过短期ATR与长期ATR的比值衡量波动率是否处于异常稳定的状态。比值低于阈值时市场缺乏波动，容易触发微小平仓亏损 (master_running_close_tiny)，因子向-1移动；比值高于阈值时市场波动正常或剧烈，因子向+1移动。",
        category="volatility", subcategory="volatility",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    import pandas as pd
    import numpy as np
    # 计算ATR
    high = data['high']
    low = data['low']
    close = data['close']
    tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
    atr_short = tr.rolling(10).mean()
    atr_long = tr.rolling(50).mean()
    ratio = atr_short / atr_long
    # 归一化到[-1,1]
    ratio = ratio.replace([np.inf, -np.inf], np.nan)
    ratio_mean = ratio.rolling(100).mean()
    ratio_std = ratio.rolling(100).std()
    z = (ratio - ratio_mean) / (ratio_std + 1e-10)
    result = np.clip(z / 3, -1, 1)  # 3倍标准差截断
    return result
