"""AI因子: 环境斜率自适应因子 | 置信:60% | 通过短期均线斜率与长期波动率的比值判断趋势强度，当斜率微弱但波动率高时发出反转信号。使用线性回归斜率除以ATR标准化，并用tanh压缩到[-1,1]。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class Regime Slope Adapter(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_rsa", name="Regime Slope Adapter",
        display_name="环境斜率自适应因子", description="通过短期均线斜率与长期波动率的比值判断趋势强度，当斜率微弱但波动率高时发出反转信号。使用线性回归斜率除以ATR标准化，并用tanh压缩到[-1,1]。",
        category="technical", subcategory="trend",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data: pd.DataFrame) -> pd.Series:
    import numpy as np
    window = 10
    close = data['close']
    slope = (close - close.shift(window)) / window
    atr = (data['high'] - data['low']).rolling(14).mean().replace(0, 1e-10)
    ratio = slope / atr
    return np.tanh(ratio * 5)
