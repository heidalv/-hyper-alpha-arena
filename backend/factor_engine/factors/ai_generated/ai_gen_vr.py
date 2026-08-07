"""AI因子: 波动率状态突变 | 置信:60% | 短期波动率与长期波动率的比值，用于捕捉市场波动状态突变（regime change）。当短期波动率远高于长期时，可能预示趋势反转或异常行情，此因子在极端值时给出负向信号（避免追涨杀跌）。计算5期与60期对数收益率的滚动标准差比值，并映射到[-1,1]区间。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class Volatility Regime Shift(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_vr", name="Volatility Regime Shift",
        display_name="波动率状态突变", description="短期波动率与长期波动率的比值，用于捕捉市场波动状态突变（regime change）。当短期波动率远高于长期时，可能预示趋势反转或异常行情，此因子在极端值时给出负向信号（避免追涨杀跌）。计算5期与60期对数收益率的滚动标准差比值，并映射到[-1,1]区间。",
        category="volatility", subcategory="volatility",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    close = data['close']
    ret = close.pct_change()
    short_vol = ret.rolling(5).std()
    long_vol = ret.rolling(60).std()
    ratio = short_vol / long_vol
    # 避免除以0
    ratio = ratio.replace([np.inf, -np.inf], np.nan)
    # 映射到[-1,1]，使用log变换后tanh
    log_ratio = np.log(ratio + 1e-6)
    result = np.tanh(log_ratio * 2)
    return result.fillna(0).clip(-1, 1)
