"""AI因子: 趋势强度 | 置信:60% | 基于价格与短期和长期移动平均线的相对位置及斜率，量化趋势明确程度。当趋势强度低于阈值时，市场处于无序震荡，容易导致小止损亏损，因此给出负向信号。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class TrendStrength(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_trdstr", name="TrendStrength",
        display_name="趋势强度", description="基于价格与短期和长期移动平均线的相对位置及斜率，量化趋势明确程度。当趋势强度低于阈值时，市场处于无序震荡，容易导致小止损亏损，因此给出负向信号。",
        category="technical", subcategory="trend",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data: pd.DataFrame) -> pd.Series:
    close = data['close']
    fast_ma = close.rolling(20).mean()
    slow_ma = close.rolling(50).mean()
    # 趋势斜率：用线性回归斜率近似，计算过去10期收盘价变化率
    slope = (close - close.shift(10)) / close.shift(10) * 100
    # 趋势强度：均线距离 + 斜率绝对值 归一化
    ma_dist = (fast_ma - slow_ma) / slow_ma * 100
    strength = ma_dist.abs() + slope.abs()
    # 滚动归一化到[-1,1] 使用z-score变换后裁剪
    z = (strength - strength.rolling(100).mean()) / strength.rolling(100).std()
    result = z.clip(-3, 3) / 3
    return result.fillna(0)
