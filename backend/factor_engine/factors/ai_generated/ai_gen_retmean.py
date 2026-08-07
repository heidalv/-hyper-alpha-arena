"""AI因子: 收益率均值回归因子 | 置信:60% | 基于短期(5日)收益率的偏度与峰度，判断市场是否处于极端情绪中。当偏度极大且峰度极高时，预示反转；将统计量映射到[-1,1]区间，偏多信号表示极度负偏态（超卖），偏空信号表示极度正偏态（超买）。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class Return_Mean_Reversion(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_retmean", name="Return_Mean_Reversion",
        display_name="收益率均值回归因子", description="基于短期(5日)收益率的偏度与峰度，判断市场是否处于极端情绪中。当偏度极大且峰度极高时，预示反转；将统计量映射到[-1,1]区间，偏多信号表示极度负偏态（超卖），偏空信号表示极度正偏态（超买）。",
        category="technical", subcategory="mean_reversion",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    import numpy as np
    close = data['close']
    ret = close.pct_change()
    window = 5
    # 滚动偏度和峰度
    skew = ret.rolling(window).skew()
    kurt = ret.rolling(window).kurt()
    # 标准化，限制异常值
    skew_norm = np.clip(skew, -3, 3) / 3
    kurt_norm = np.clip(kurt, -2, 10) / 10  # 峰度通常非负，但允许负值
    # 组合信号：负偏度+高峰度 -> 超卖(正向)，正偏度+高峰度 ->超买(负向)
    signal = -skew_norm * (1 + kurt_norm) * 0.5
    return pd.Series(np.clip(signal, -1, 1), index=data.index)
