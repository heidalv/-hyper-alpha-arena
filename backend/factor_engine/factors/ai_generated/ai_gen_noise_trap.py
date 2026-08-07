"""AI因子: 噪声陷阱指标 | 置信:65% | 衡量市场在低波动下异常放量，容易产生伪趋势导致亏损。计算ATR/close占比与成交量变化率，当低波动且成交量激增时返回负值。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class Noise Trap Indicator(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_noise_trap", name="Noise Trap Indicator",
        display_name="噪声陷阱指标", description="衡量市场在低波动下异常放量，容易产生伪趋势导致亏损。计算ATR/close占比与成交量变化率，当低波动且成交量激增时返回负值。",
        category="composite", subcategory="volatility",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    import numpy as np
    # ATR
    high, low, close = data['high'], data['low'], data['close']
    tr = np.maximum(high - low, np.abs(high - close.shift(1)), np.abs(low - close.shift(1)))
    atr = tr.rolling(14).mean()
    atr_ratio = atr / close
    # volume surge
    vol = data['volume']
    vol_ma = vol.rolling(20).mean()
    vol_ratio = vol / (vol_ma + 1e-10)
    # noise condition: low volatility (<20th percentile) and volume surge (>1.5)
    atr_low = (atr_ratio < atr_ratio.rolling(60).quantile(0.2))
    vol_high = (vol_ratio > 1.5)
    noise = atr_low & vol_high
    # score: -1 when noise trap, otherwise scaled vol_ratio
    result = np.where(noise, -1.0, 0.0)
    # smooth binary to continuous with decay
    result = result.rolling(3).mean().fillna(0.0)
    return result.clip(-1, 1)
