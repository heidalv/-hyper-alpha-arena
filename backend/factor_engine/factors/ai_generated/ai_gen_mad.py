"""AI因子: 均值回归偏离度 | 置信:60% | 衡量当前价格相对于中期移动平均线的标准化距离（z-score），用于识别极端超买或超卖状态。当价格远离均值时，反转概率增大，但亏损模式显示可能追高，故在极端正值时给予负向信号（看跌），极端负值时看涨。使用60期均线和标准差计算。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class Mean Reversion Distance Z-score(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_mad", name="Mean Reversion Distance Z-score",
        display_name="均值回归偏离度", description="衡量当前价格相对于中期移动平均线的标准化距离（z-score），用于识别极端超买或超卖状态。当价格远离均值时，反转概率增大，但亏损模式显示可能追高，故在极端正值时给予负向信号（看跌），极端负值时看涨。使用60期均线和标准差计算。",
        category="mean_reversion", subcategory="mean_reversion",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    close = data['close']
    ma = close.rolling(60).mean()
    std = close.rolling(60).std()
    z = (close - ma) / (std + 1e-6)
    # 用tanh限制范围并保留符号
    result = np.tanh(z * 1.5)
    return result.fillna(0).clip(-1, 1)
