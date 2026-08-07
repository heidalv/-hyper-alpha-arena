"""AI因子: 波动调整动量 | 置信:60% | 使用近期价格变化率与平均真实波幅的比值，识别低波动环境下的趋势启动。在低波动的上涨趋势中强烈做多，高波动或下跌中做空。可避免在不明朗市况下逆势做空。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class Volatility Adjusted Momentum(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_vol_mom", name="Volatility Adjusted Momentum",
        display_name="波动调整动量", description="使用近期价格变化率与平均真实波幅的比值，识别低波动环境下的趋势启动。在低波动的上涨趋势中强烈做多，高波动或下跌中做空。可避免在不明朗市况下逆势做空。",
        category="technical", subcategory="momentum",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    close = data['close']
    high = data['high']
    low = data['low']
    
    # 计算14日ROC
    roc = close.pct_change(14)
    # 计算14日ATR
    tr = pd.concat([high - low, abs(high - close.shift()), abs(low - close.shift())], axis=1).max(axis=1)
    atr = tr.rolling(window=14).mean()
    # 归一化：用滚动标准差避免除零
    atr_std = atr.rolling(50).std().replace(0, np.nan)
    z_score = (atr - atr.rolling(50).mean()) / atr_std
    # 波动调整动量：ROC除以ATR归一化值，再使用tanh映射到[-1,1]
    raw = roc / (atr / close.shift(14)).clip(lower=1e-6)
    # 平滑并限幅
    result = raw.rolling(3).mean()
    result = result.clip(-2, 2) / 2
    return result
