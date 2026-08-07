"""AI因子: 波动率状态指标 | 置信:60% | 通过比较近期波动率与长期波动率，识别低波动盘整区间。当波动率急速收缩且价格窄幅震荡时，预示可能出现假突破风险（regime=unknown），因子接近-1；高波动趋势市场因子接近+1。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class Volatility Regime Indicator(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_volatility_regime", name="Volatility Regime Indicator",
        display_name="波动率状态指标", description="通过比较近期波动率与长期波动率，识别低波动盘整区间。当波动率急速收缩且价格窄幅震荡时，预示可能出现假突破风险（regime=unknown），因子接近-1；高波动趋势市场因子接近+1。",
        category="technical", subcategory="volatility",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    df = data.copy()
    close = df['close']
    high = df['high']
    low = df['low']
    
    # 计算价格变化率
    returns = close.pct_change()
    
    # 短期波动率 (5周期)
    vol_short = returns.rolling(5).std()
    # 长期波动率 (20周期)
    vol_long = returns.rolling(20).std()
    
    # 波动率比率，平滑处理
    vol_ratio = vol_short / (vol_long + 1e-10)
    
    # 价格区间宽度：近期高低价差相对均线
    price_range = (high - low) / close.rolling(10).mean()
    range_ma = price_range.rolling(20).mean()
    range_deviation = (price_range - range_ma) / (range_ma + 1e-10)
    
    # 当波动率急剧收缩且价格区间狭窄时，信号为负
    vol_signal = -vol_ratio  # 收缩（ratio<1）得正？实际上vol_ratio小代表收缩，我们希望信号为负，所以用-1*vol_ratio？
    # 更合理：vol_ratio < 0.5 且 range_deviation < -0.5 时强烈负信号
    # 改用组合
    vol_comp = (vol_ratio - 1).clip(-1, 1)  # 当vol_ratio>1时为正（高波动），<1时为负
    range_comp = range_deviation.clip(-1, 1)
    factor = 0.6 * vol_comp + 0.4 * range_comp
    factor = factor.clip(-1, 1)
    return factor
