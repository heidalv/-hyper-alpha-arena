"""AI因子: 波动率状态因子 | 置信:60% | 比较当前ATR与过去100周期ATR的中位数，当ATR低于中位数50%时认为低波动（输出0），否则根据价格相对于布林带中轨方向输出±1，高波动时方向更明确。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class Volatility_Regime_ATR(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_vol_reg", name="Volatility_Regime_ATR",
        display_name="波动率状态因子", description="比较当前ATR与过去100周期ATR的中位数，当ATR低于中位数50%时认为低波动（输出0），否则根据价格相对于布林带中轨方向输出±1，高波动时方向更明确。",
        category="technical", subcategory="volatility",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    import numpy as np
    import pandas as pd
    high = data['high']
    low = data['low']
    close = data['close']
    # ATR
    period = 14
    tr = np.maximum(high - low, np.maximum(abs(high - close.shift(1)), abs(low - close.shift(1))))
    atr = tr.rolling(period).mean()
    # 中位数ATR过去100期
    med_atr = atr.rolling(100).median()
    # 布林带中轨（20日均线）
    ma20 = close.rolling(20).mean()
    # 信号
    result = pd.Series(0.0, index=data.index)
    # 当ATR大于中位数时（高波动），根据价格相对均线方向
    high_vol_mask = atr > (0.5 * med_atr)  # 至少是50%中位数，避免极端低波
    bull = (close > ma20) & high_vol_mask
    bear = (close < ma20) & high_vol_mask
    result[bull] = 1.0
    result[bear] = -1.0
    # 低波动时保留0
    return result
