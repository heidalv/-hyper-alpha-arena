"""AI因子: 趋势衰减指标 | 置信:60% | 基于效率比率（Efficiency Ratio）和ADX的变形，衡量趋势的强弱。当趋势强度快速衰减时，容易导致持仓超时或止损，尤其适用于空头。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class Trend Weakness Indicator(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_trend_weak", name="Trend Weakness Indicator",
        display_name="趋势衰减指标", description="基于效率比率（Efficiency Ratio）和ADX的变形，衡量趋势的强弱。当趋势强度快速衰减时，容易导致持仓超时或止损，尤其适用于空头。",
        category="technical", subcategory="trend",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    import pandas as pd
    import numpy as np
    
    close = data['close']
    high = data['high']
    low = data['low']
    
    period = 14
    
    # 效率比率：方向变动 / 总波动
    direction = close.diff(period).abs()
    volatility = (high - low).rolling(period).sum()
    efficiency = direction / (volatility + 1e-10)
    
    # 趋势强度的变化率
    eff_roc = efficiency.diff(3) / (efficiency.rolling(3).mean() + 1e-10)
    
    # 当效率比率下降且低于阈值时，表示趋势走弱
    weak_trend = (efficiency < 0.3) & (eff_roc < -0.2)
    
    # 同时结合ADX（简化）
    # 计算+DI和-DI
    tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(period).mean()
    up_move = high - high.shift()
    down_move = low.shift() - low
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0)
    plus_di = pd.Series(plus_dm).rolling(period).sum() / (atr + 1e-10) * 100
    minus_di = pd.Series(minus_dm).rolling(period).sum() / (atr + 1e-10) * 100
    dx = (plus_di - minus_di).abs() / (plus_di + minus_di + 1e-10) * 100
    adx = dx.rolling(period).mean()
    
    adx_weak = adx < 25
    
    # 组合信号：趋势强度弱且下降
    combined = weak_trend | (adx_weak & (adx.diff() < -2))
    
    # 根据原始趋势方向赋值：下降趋势中趋势弱则做多（+1），上升趋势中趋势弱则做空（-1）
    trend_direction = 1 if close.iloc[-1] > close.iloc[-period] else -1  # 简化，实际需要逐行判断
    # 使用滚动线性回归斜率判断短期趋势方向
    def slope(series):
        x = np.arange(len(series))
        y = series.values
        if len(y) < 2:
            return 0
        return np.polyfit(x, y, 1)[0]
    
    slope_series = close.rolling(10).apply(slope, raw=False)
    
    factor = pd.Series(0, index=data.index)
    # 上升趋势中趋势弱 => -1
    factor[(slope_series > 0) & combined] = -1.0
    # 下降趋势中趋势弱 => +1
    factor[(slope_series < 0) & combined] = 1.0
    
    result = factor.rolling(5).mean().fillna(0)
    return result
