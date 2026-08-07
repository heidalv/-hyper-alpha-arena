"""AI因子: 趋势一致性指标 | 置信:60% | 通过比较短期和长期价格方向的一致性，衡量市场是否处于明确趋势中。当短期趋势与长期趋势背离时（如震荡或反转），值为负；一致时为正。用于避免趋势不明朗的行情。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class Trend Consistency Index(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_trend_conf", name="Trend Consistency Index",
        display_name="趋势一致性指标", description="通过比较短期和长期价格方向的一致性，衡量市场是否处于明确趋势中。当短期趋势与长期趋势背离时（如震荡或反转），值为负；一致时为正。用于避免趋势不明朗的行情。",
        category="composite", subcategory="trend",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    # 计算短期和长期移动平均
    sma_short = data['close'].rolling(window=5).mean()
    sma_long = data['close'].rolling(window=20).mean()
    # 计算方向一致性：短期均线斜率与长期均线斜率的相关系数
    short_slope = sma_short.diff(1)
    long_slope = sma_long.diff(1)
    # 使用滚动相关系数
    corr = short_slope.rolling(window=10).corr(long_slope)
    # 归一化到[-1,1]
    result = corr.fillna(0).clip(-1, 1)
    return result
