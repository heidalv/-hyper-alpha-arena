"""AI因子: 波动率异常反转因子 | 置信:65% | 计算过去20日波动率（ATR/收盘价）的Z-score，当Z-score低于-1.5时表示波动率极低，容易产生假突破后的反转，信号为负向（假设做多反向）。返回[-1,+1]：负值表示低波动率时预期反转，正值表示正常波动率。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class Volatility Z-score Reversal(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_vol_zscore", name="Volatility Z-score Reversal",
        display_name="波动率异常反转因子", description="计算过去20日波动率（ATR/收盘价）的Z-score，当Z-score低于-1.5时表示波动率极低，容易产生假突破后的反转，信号为负向（假设做多反向）。返回[-1,+1]：负值表示低波动率时预期反转，正值表示正常波动率。",
        category="technical", subcategory="volatility",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    # data: DataFrame with columns open,high,low,close,volume
    import pandas as pd
    import numpy as np
    # 计算ATR
    high = data['high']
    low = data['low']
    close = data['close']
    tr = pd.concat([high - low, (high - close.shift(1)).abs(), (low - close.shift(1)).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean()
    # 波动率 = atr / close
    vol = atr / close
    # Z-score over 20 days
    mean = vol.rolling(20).mean()
    std = vol.rolling(20).std()
    z = (vol - mean) / std
    # 映射到[-1,1]: 当z<-1.5时取-1，z>1.5时取1，否则线性插值
    result = pd.Series(0.0, index=data.index)
    lower_thresh = -1.5
    upper_thresh = 1.5
    result = np.clip((z - lower_thresh) / (upper_thresh - lower_thresh) * 2 - 1, -1, 1)
    # 反转方向：极低波动率预期向下反转，所以乘-1（如果希望上涨则反转信号）
    result = -result
    return result
