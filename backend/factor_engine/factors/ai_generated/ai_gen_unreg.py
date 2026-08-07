"""AI因子: 未知市况识别因子 | 置信:55% | 基于近期波动率的变化率和趋势强度，识别处于无明显趋势或波动率异常（未知市况）状态。当波动率变异系数高且趋势强度弱时，因子输出负值（建议谨慎或反向操作）。使用20日滚动标准差和移动平均的比值。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class UnregimeDetector(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_unreg", name="UnregimeDetector",
        display_name="未知市况识别因子", description="基于近期波动率的变化率和趋势强度，识别处于无明显趋势或波动率异常（未知市况）状态。当波动率变异系数高且趋势强度弱时，因子输出负值（建议谨慎或反向操作）。使用20日滚动标准差和移动平均的比值。",
        category="behavioral", subcategory="volatility",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    import numpy as np
    # 计算收益率
    ret = data['close'].pct_change()
    # 波动率（20日标准差）
    vol = ret.rolling(20, min_periods=10).std()
    # 波动率的变化率（当前与20日均值的差异）
    vol_mean = vol.rolling(20, min_periods=10).mean()
    vol_cv = np.where(vol_mean > 0, vol / (vol_mean + 1e-10), 0)  # 变异系数
    # 趋势强度：用收盘价与20日均线的距离占比
    ma20 = data['close'].rolling(20, min_periods=10).mean()
    trend_strength = (data['close'] - ma20).abs() / (data['close'] + 1e-10)
    # 未知市况：高波动变异系数且低趋势强度
    high_vol_cv = (vol_cv > 1.5).astype(float)
    low_trend = (trend_strength < 0.02).astype(float)
    signal = high_vol_cv * low_trend
    # 出现信号时输出-1，否则0
    result = -signal
    return result.clip(-1, 1)
