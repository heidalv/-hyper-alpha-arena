"""AI因子: 价格位置Z得分 | 置信:60% | 计算当日收盘价在K线高低范围内的相对位置，并对其过去20个周期进行Z-score标准化。极端正/负值表示收盘位置异常，可能预示市场情绪过度或趋势衰竭，常在未知regime中导致亏损。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class Price Position Z-score(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_pp", name="Price Position Z-score",
        display_name="价格位置Z得分", description="计算当日收盘价在K线高低范围内的相对位置，并对其过去20个周期进行Z-score标准化。极端正/负值表示收盘位置异常，可能预示市场情绪过度或趋势衰竭，常在未知regime中导致亏损。",
        category="technical", subcategory="mean_reversion",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    import pandas as pd
    import numpy as np
    high = data['high']
    low = data['low']
    close = data['close']
    range_ = high - low
    pos = (close - low) / range_.replace(0, np.nan)
    pos = pos.fillna(0.5)
    mean_pos = pos.rolling(20).mean()
    std_pos = pos.rolling(20).std()
    z = (pos - mean_pos) / std_pos.replace(0, np.nan)
    z = z.fillna(0)
    factor = np.clip(z, -3, 3) / 3.0
    return factor
