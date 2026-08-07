"""AI因子: 趋势清晰度指数 | 置信:65% | 综合短期均线与长期均线的斜率一致性、价格偏离均线的幅度以及成交量确认，判断当前趋势是否清晰可靠。正值表示趋势明确（适合顺势），负值表示趋势模糊或震荡（避免入场）。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class Trend Clarity Index(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_trend_clarity", name="Trend Clarity Index",
        display_name="趋势清晰度指数", description="综合短期均线与长期均线的斜率一致性、价格偏离均线的幅度以及成交量确认，判断当前趋势是否清晰可靠。正值表示趋势明确（适合顺势），负值表示趋势模糊或震荡（避免入场）。",
        category="composite", subcategory="trend",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    close = data['close']
    volume = data['volume']
    ma_fast = close.rolling(10).mean()
    ma_slow = close.rolling(30).mean()
    # 斜率差异：快速均线变化率与慢速均线变化率的差值标准化
    slope_fast = ma_fast.diff(3) / ma_fast.shift(3)
    slope_slow = ma_slow.diff(5) / ma_slow.shift(5)
    slope_diff = (slope_fast - slope_slow).clip(-0.05, 0.05) / 0.05
    # 价格偏离均线程度
    deviation = (close - ma_slow) / (close.rolling(20).std() + 1e-10)
    deviation_norm = deviation.clip(-3, 3) / 3
    # 成交量确认：短期成交量相对于长期均值
    vol_ratio = volume / volume.rolling(20).mean()
    vol_factor = (vol_ratio - 1).clip(-1, 1)
    # 综合：趋势一致时赋予正权重，震荡或背离时负权重
    result = slope_diff * 0.4 + deviation_norm * 0.4 + vol_factor * 0.2
    result = result.clip(-1, 1)
    return result
