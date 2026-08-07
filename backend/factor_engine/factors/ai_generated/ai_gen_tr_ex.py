"""AI因子: 趋势衰竭振荡器 | 置信:60% | 基于线性回归斜率变化与波动率比值，判断趋势动能是否衰竭。当趋势斜率放缓但波动率仍高时，趋势可能反转。从亏损数据看，做空后价格反弹发生在趋势末端，因此该因子负值表示空头趋势衰竭（应避免做空），正值表示多头趋势衰竭（可做空）。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class Trend Exhaustion Oscillator(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_tr_ex", name="Trend Exhaustion Oscillator",
        display_name="趋势衰竭振荡器", description="基于线性回归斜率变化与波动率比值，判断趋势动能是否衰竭。当趋势斜率放缓但波动率仍高时，趋势可能反转。从亏损数据看，做空后价格反弹发生在趋势末端，因此该因子负值表示空头趋势衰竭（应避免做空），正值表示多头趋势衰竭（可做空）。",
        category="technical", subcategory="trend",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    import pandas as pd
    import numpy as np
    # 参数
    window = 14
    # 计算线性回归斜率（使用close价格）
    def slope(series):
        x = np.arange(len(series))
        if len(series) < 2:
            return 0
        slope, _ = np.polyfit(x, series, 1)
        return slope
    # 滚动斜率
    slopes = data['close'].rolling(window, min_periods=window).apply(slope, raw=False)
    # 斜率变化：当前斜率 - 前一斜率
    slope_change = slopes.diff()
    # 波动率：ATR相对收盘价
    tr = np.maximum(data['high'] - data['low'], np.maximum(abs(data['high'] - data['close'].shift(1)), abs(data['low'] - data['close'].shift(1))))
    atr = tr.rolling(window, min_periods=1).mean() / (data['close'] + 1e-10)
    # 衰竭指标 = 斜率变化 * atr  (斜率放缓+高波动=衰竭)
    raw = -slope_change * atr  # 负斜率变化对应空头衰竭，乘以atr后正值表示空头衰竭
    result = np.tanh(raw * 5)  # 放大后tanh映射到[-1,1]
    result = result.fillna(0)
    return pd.Series(result, index=data.index)
