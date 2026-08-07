"""AI因子: 反转风险指标 | 置信:60% | 通过价格偏离均线的程度与相对强弱指标结合，识别潜在的过度拉伸反转风险。当价格远离均线且RSI处于极端区域时发出反向信号，适合过滤逆势开仓。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class Reversal_Risk(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_revrisk", name="Reversal_Risk",
        display_name="反转风险指标", description="通过价格偏离均线的程度与相对强弱指标结合，识别潜在的过度拉伸反转风险。当价格远离均线且RSI处于极端区域时发出反向信号，适合过滤逆势开仓。",
        category="behavioral", subcategory="mean_reversion",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    close = data['close']
    high = data['high']
    low = data['low']
    # 价格偏离20日均线百分比
    ma20 = close.rolling(20).mean()
    deviation = (close - ma20) / (ma20 + 1e-10)
    # RSI 14
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.rolling(14).mean()
    avg_loss = loss.rolling(14).mean()
    rs = avg_gain / (avg_loss + 1e-10)
    rsi = 100 - (100 / (1 + rs))
    # RSI极端信号：>70超买，<30超卖
    extreme_up = (rsi - 70) / 30.0  # 0~1
    extreme_dn = (30 - rsi) / 30.0  # 0~1
    # 结合偏离度：正向偏离+超买 => 负信号(看跌)，负向偏离+超卖 => 正信号(看涨)
    risk_signal = -np.clip(deviation, -0.1, 0.1) * extreme_up + np.clip(-deviation, -0.1, 0.1) * extreme_dn
    result = np.tanh(risk_signal * 10)
    return result.fillna(0.0)
