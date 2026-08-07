"""AI因子: 价格收敛因子 | 置信:65% | 度量当前价格与短期均线（如20周期）的相对距离，结合ATR标准化，当价格从远离均线向均线收敛时给出正向信号，预期均值回归。适用于识别震荡行情中因过度偏离导致的回调机会，针对'close_tiny'模式中反向波动亏损设计。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class Price Convergence to Short-term Mean(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_conv", name="Price Convergence to Short-term Mean",
        display_name="价格收敛因子", description="度量当前价格与短期均线（如20周期）的相对距离，结合ATR标准化，当价格从远离均线向均线收敛时给出正向信号，预期均值回归。适用于识别震荡行情中因过度偏离导致的回调机会，针对'close_tiny'模式中反向波动亏损设计。",
        category="technical", subcategory="mean_reversion",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    # data: pd.DataFrame with columns open, high, low, close, volume
    import numpy as np
    # 计算短期均线（20周期）
    sma20 = data['close'].rolling(21).mean()
    # 计算ATR（14周期）
    tr = np.maximum(data['high'] - data['low'], np.maximum(abs(data['high'] - data['close'].shift(1)), abs(data['low'] - data['close'].shift(1))))
    atr14 = tr.rolling(14).mean()
    # 价格偏离度标准化
    dist = (data['close'] - sma20) / (atr14 + 1e-10)
    # 限制范围[-1,1]并反转符号：当dist绝对值变小（收敛）时为正信号
    # 使用e^(-|dist|) 但需要[-1,1]，改为- sign(dist)*min(abs(dist),1) 表示发散方向
    # 但我们希望收敛信号为正，所以计算最近两期dist的变化方向
    dist_change = dist.diff()
    # 如果dist绝对值减小，则收敛信号为正。sign = (abs(dist) - abs(dist.shift(1))) < 0 ? 1 : -1
    conv_signal = np.where(abs(dist) < abs(dist.shift(1)), 1, -1)
    # 再乘以幅度调制
    factor = pd.Series(conv_signal * (1 - np.minimum(abs(dist), 1)), index=data.index)
    factor = factor.fillna(0).clip(-1, 1)
    return factor
