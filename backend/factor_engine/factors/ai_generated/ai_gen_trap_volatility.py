"""AI因子: 陷阱波动因子 | 置信:60% | 计算过去N根K线的平均真实波幅(ATR)与价格区间最大值-最小值的比值，比值越小说明价格越紧凑，容易引发突破陷阱。归一化后输出[-1,1]，紧凑区域为负值。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class trap_volatility(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_trap_volatility", name="trap_volatility",
        display_name="陷阱波动因子", description="计算过去N根K线的平均真实波幅(ATR)与价格区间最大值-最小值的比值，比值越小说明价格越紧凑，容易引发突破陷阱。归一化后输出[-1,1]，紧凑区域为负值。",
        category="behavioral", subcategory="volatility",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    # data: pd.DataFrame with columns open, high, low, close, volume
    import numpy as np
    import pandas as pd
    
    N = 20
    # 计算真实波幅
    high = data['high'].values
    low = data['low'].values
    close = data['close'].values
    prev_close = np.roll(close, 1)
    prev_close[0] = close[0]
    tr = np.maximum(high - low, np.maximum(np.abs(high - prev_close), np.abs(low - prev_close)))
    atr = pd.Series(tr).rolling(window=N).mean().values
    
    # 价格区间宽度
    range_width = high - low
    range_ma = pd.Series(range_width).rolling(window=N).mean().values
    
    # 比值，避免除零
    ratio = np.where(range_ma > 1e-10, atr / range_ma, 0)
    
    # 归一化到[-1,1]：使用历史分位数或简单缩放
    # 这里使用z-score然后tanh压缩
    rolling_mean = pd.Series(ratio).rolling(window=100, min_periods=50).mean().values
    rolling_std = pd.Series(ratio).rolling(window=100, min_periods=50).std().values
    # 防止std为0
    rolling_std = np.where(rolling_std < 1e-10, 1e-10, rolling_std)
    z = (ratio - rolling_mean) / rolling_std
    result = np.clip(np.tanh(z), -1, 1)
    return pd.Series(result, index=data.index)
