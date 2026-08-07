"""AI因子: 价格方向混乱因子 | 置信:60% | 计算日内振幅与收盘方向的比值，若振幅大但收盘接近开盘（即方向不明显），表明多空拉锯，市场处于无趋势的混乱状态，因子输出负值。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class Price Confusion Index(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_price_confusion", name="Price Confusion Index",
        display_name="价格方向混乱因子", description="计算日内振幅与收盘方向的比值，若振幅大但收盘接近开盘（即方向不明显），表明多空拉锯，市场处于无趋势的混乱状态，因子输出负值。",
        category="behavioral", subcategory="mean_reversion",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    import numpy as np
    amplitude = data['high'] - data['low']
    direction = np.abs(data['close'] - data['open'])
    ratio = amplitude / (direction + 1e-10)
    # 当ratio很大且direction很小时，混乱度最高
    # 用ratio的反函数？使用log压缩，然后归一化
    log_ratio = np.log(ratio + 1e-10)
    # 滚动标准化
    mean = log_ratio.rolling(20).mean()
    std = log_ratio.rolling(20).std()
    zscore = (log_ratio - mean) / (std + 1e-10)
    raw = np.clip(zscore * 0.5, -3, 3)
    result = -np.tanh(raw)  # 高混乱度（高ratio）对应负值
    return result
