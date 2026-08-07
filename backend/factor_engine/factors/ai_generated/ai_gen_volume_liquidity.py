"""AI因子: 流动性枯竭因子 | 置信:60% | 基于Amihud非流动性指标，衡量价格波动对成交量的敏感度；当价格波动大但成交量明显偏低时，市场流动性不足，方向不明，因子输出负值。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class Liquidity Drought Indicator(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_volume_liquidity", name="Liquidity Drought Indicator",
        display_name="流动性枯竭因子", description="基于Amihud非流动性指标，衡量价格波动对成交量的敏感度；当价格波动大但成交量明显偏低时，市场流动性不足，方向不明，因子输出负值。",
        category="composite", subcategory="volume",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    import numpy as np
    ret = np.log(data['close'] / data['close'].shift(1)).abs()
    volume = data['volume']
    illiquidity = ret / (volume + 1e-10)
    # 滚动中位数平滑并标准化
    roll_med = illiquidity.rolling(20).median()
    # 使用Z-score方法，然后tanh映射到[-1,1]
    zscore = (illiquidity - roll_med) / (illiquidity.rolling(20).std() + 1e-10)
    raw = np.clip(zscore * 0.5, -3, 3)  # 限制极端值
    result = -np.tanh(raw)  # 高非流动性对应负值
    return result
