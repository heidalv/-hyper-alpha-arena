"""AI因子: 短期反转动量因子 | 置信:60% | 基于最近N根K线的价格极值位置和成交量对比，当价格在短期高点/低点附近且成交量放大时，预测价格反向运动。针对'master_running_close_tiny'中频繁开仓后反向大幅亏损设计，捕捉极端位置的反转信号。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class Short-term Reversal Momentum(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_reversal", name="Short-term Reversal Momentum",
        display_name="短期反转动量因子", description="基于最近N根K线的价格极值位置和成交量对比，当价格在短期高点/低点附近且成交量放大时，预测价格反向运动。针对'master_running_close_tiny'中频繁开仓后反向大幅亏损设计，捕捉极端位置的反转信号。",
        category="behavioral", subcategory="contrarian",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    import numpy as np
    # 参数
    n = 5  # 短期窗口
    # 计算当前价格在最近n根的高低位置
    high_n = data['high'].rolling(n).max()
    low_n = data['low'].rolling(n).min()
    # 位置比率 (接近1为高，接近0为低)
    pos = (data['close'] - low_n) / (high_n - low_n + 1e-10)
    # 成交量变化率
    vol_ratio = data['volume'] / data['volume'].rolling(n).mean()
    # 反转信号：当价格在极值（>0.8或<0.2）且成交量放大（>1.2）时，预测反向
    # 上极端做空信号，下极端做多信号
    signal = np.where((pos > 0.8) & (vol_ratio > 1.2), -1 * (pos - 0.8) / 0.2,
                      np.where((pos < 0.2) & (vol_ratio > 1.2), (0.2 - pos) / 0.2, 0))
    factor = pd.Series(signal, index=data.index).fillna(0).clip(-1, 1)
    return factor
