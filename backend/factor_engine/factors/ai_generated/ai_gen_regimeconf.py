"""AI因子: 市场状态置信度得分 | 置信:55% | 量化市场是否处于可识别的趋势或振荡状态，规避'unknown' regime。通过计算价格序列的赫斯特指数近似值和自相关强度，判断市场是趋势、均值回归还是随机游走。输出[-1,1]，接近1表示强趋势（状态明确），接近-1表示强均值回归（状态明确），0表示随机游走（状态不明）。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class Regime_Confidence_Score(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_regimeconf", name="Regime_Confidence_Score",
        display_name="市场状态置信度得分", description="量化市场是否处于可识别的趋势或振荡状态，规避'unknown' regime。通过计算价格序列的赫斯特指数近似值和自相关强度，判断市场是趋势、均值回归还是随机游走。输出[-1,1]，接近1表示强趋势（状态明确），接近-1表示强均值回归（状态明确），0表示随机游走（状态不明）。",
        category="behavioral", subcategory="trend",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    import numpy as np
    close = data['close']
    ret = close.pct_change().dropna()
    # 用滚动20期计算序列的长期记忆性
    def hurst_exponent(ts):
        if len(ts) < 10:
            return 0.5
        # 简化计算: 使用R/S方法
        mean = np.mean(ts)
        dev = ts - mean
        cumsum = np.cumsum(dev)
        R = np.max(cumsum) - np.min(cumsum)
        S = np.std(ts, ddof=1)
        if S == 0:
            return 0.5
        return np.log(R / S) / np.log(len(ts))
    # 滚动计算赫斯特指数
    hurst = ret.rolling(window=20).apply(lambda x: hurst_exponent(x.values), raw=False)
    # 一阶自相关系数
    autocorr = ret.rolling(20).apply(lambda x: x.autocorr() if len(x.dropna()) > 5 else 0, raw=False)
    # 综合得分: 赫斯特 > 0.5 且自相关为正 => 趋势; <0.5 且自相关为负 => 均值回归
    trend_score = (hurst - 0.5) * 4  # 映射到[-2,2] 大致
    autocorr_score = autocorr.fillna(0) * 2
    raw = trend_score + autocorr_score
    # 归一化到[-1,1]
    result = np.tanh(raw)
    result = result.fillna(0).clip(-1, 1)
    return result
