"""AI因子: 成交量效率指标 | 置信:60% | 结合成交量变异系数和价格效率（日内波动与方向性收益之比），识别噪声驱动的无效率行情。当成交量波动大而价格效率低（涨跌不明显）时输出负值，建议避免交易。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class Volume Efficiency Indicator(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_vei", name="Volume Efficiency Indicator",
        display_name="成交量效率指标", description="结合成交量变异系数和价格效率（日内波动与方向性收益之比），识别噪声驱动的无效率行情。当成交量波动大而价格效率低（涨跌不明显）时输出负值，建议避免交易。",
        category="behavioral", subcategory="volume",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    open = data['open']
    high = data['high']
    low = data['low']
    close = data['close']
    volume = data['volume']
    # 成交量变异系数（CV）：20日滚动标准差/均值
    vol_mean = volume.rolling(20).mean()
    vol_std = volume.rolling(20).std()
    cv = vol_std / (vol_mean + 1e-10)
    # 价格效率：|close - open| / (high - low + 1e-10)，日内方向性收益占总波动的比例
    efficiency = (close - open).abs() / (high - low + 1e-10)
    # 复合指标：高CV + 低效率 => 噪声大 => 负值
    # 对两个指标分别标准化
    cv_z = (cv - cv.rolling(50).mean()) / (cv.rolling(50).std() + 1e-10)
    eff_z = (efficiency - efficiency.rolling(50).mean()) / (efficiency.rolling(50).std() + 1e-10)
    # 复合：高成交量波动（正cv_z）且低效率（负eff_z）时得分低
    composite = -cv_z + eff_z  # 注意：-cv_z使高cv_z变负，+eff_z使低eff_z变负
    # 映射到[-1,1]
    result = np.tanh(composite)
    result = result.fillna(0.0)
    return result
