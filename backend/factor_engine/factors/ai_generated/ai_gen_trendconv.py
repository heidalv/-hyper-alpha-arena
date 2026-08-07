"""AI因子: 趋势收敛指示器 | 置信:60% | 检测价格在均线附近窄幅整理且波动率收缩的情形，此时一旦突破方向确立，原有趋势会加速，做空亏损风险增加。因子结合布林带宽度与价格相对位置，输出[-1,1]，正值表示突破向上可能。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class Trend Convergence Indicator(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_trendconv", name="Trend Convergence Indicator",
        display_name="趋势收敛指示器", description="检测价格在均线附近窄幅整理且波动率收缩的情形，此时一旦突破方向确立，原有趋势会加速，做空亏损风险增加。因子结合布林带宽度与价格相对位置，输出[-1,1]，正值表示突破向上可能。",
        category="technical", subcategory="volatility",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    import pandas as pd
    import numpy as np
    n = 20
    sma = data['close'].rolling(n).mean()
    std = data['close'].rolling(n).std()
    upper = sma + 2 * std
    lower = sma - 2 * std
    # 相对位置 (价格在布林带内的位置)
    position = (data['close'] - lower) / (upper - lower + 1e-10)  # 0-1
    # 布林带宽度变化率 (收缩为正)
    bandwidth = (upper - lower) / sma
    bw_change = bandwidth.pct_change(3).fillna(0)
    # 收缩时且价格靠近上轨 -> 向上突破可能
    raw = (position - 0.5) * np.sign(bw_change)  # bw_change负表示收缩，但我们要收缩时position>0.5向上
    # 修正：bw_change负表示变窄，此时若position>0.5则向上突破概率大
    # 改为：当bw_change<0且position>0.6时信号正，bw_change<0且position<0.4时信号负
    signal = np.where((bw_change < -0.01) & (position > 0.6), 1,
                      np.where((bw_change < -0.01) & (position < 0.4), -1, 0))
    # 平滑
    result = pd.Series(signal, index=data.index).rolling(3).mean().fillna(0)
    return result
