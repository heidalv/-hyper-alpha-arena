"""AI因子: 持仓时间衰竭 | 置信:60% | 基于价格走势的持续性预测持仓超时风险（如max_hold_timeout亏损）。通过计算短期趋势强度（如20周期线性回归斜率）与价格通道宽度（布林带带宽）的背离，当趋势斜率走平而通道收窄时，表明行情进入盘整，继续持仓容易超时消耗。输出-1表示强烈避免持仓（盘整），+1表示趋势有延续性。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class Hold Time Exhaustion(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_hold_time_risk", name="Hold Time Exhaustion",
        display_name="持仓时间衰竭", description="基于价格走势的持续性预测持仓超时风险（如max_hold_timeout亏损）。通过计算短期趋势强度（如20周期线性回归斜率）与价格通道宽度（布林带带宽）的背离，当趋势斜率走平而通道收窄时，表明行情进入盘整，继续持仓容易超时消耗。输出-1表示强烈避免持仓（盘整），+1表示趋势有延续性。",
        category="composite", subcategory="trend",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    close = data['close']
    high = data['high']
    low = data['low']
    # 线性回归斜率（20周期）
    def slope(series, period=20):
        x = np.arange(period)
        y = series.values[-period:]
        if len(y) < period:
            return np.nan
        slope_val = np.polyfit(x, y, 1)[0]
        return slope_val
    slope_series = close.rolling(20).apply(lambda s: slope(s, 20), raw=False)
    # 布林带宽度
    sma = close.rolling(20).mean()
    std = close.rolling(20).std()
    bandwidth = (2 * std) / sma
    # 背离：斜率绝对值变小且带宽缩小 -> 盘整
    slope_norm = slope_series / (close.rolling(20).mean() + 1e-8)  # 相对价格归一化
    # 计算斜率变化
    slope_change = slope_norm - slope_norm.shift(5)
    band_change = bandwidth - bandwidth.shift(5)
    # 组合信号
    hold_risk = (slope_change * -1) + (band_change * -1)
    z = (hold_risk - hold_risk.rolling(20).mean()) / (hold_risk.rolling(20).std() + 1e-8)
    result = np.clip(z, -3, 3) / 3
    return result.fillna(0.0)
