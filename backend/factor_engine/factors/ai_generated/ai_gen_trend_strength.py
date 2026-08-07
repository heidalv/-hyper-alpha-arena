"""AI因子: 趋势强度评分 | 置信:65% | 基于ADX和价格相对于均线位置，衡量市场趋势的强弱。当ADX低于阈值且价格在均线附近时，认为是regime=unknown的弱趋势状态，因子接近-1；强趋势时接近+1。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class Trend Strength Score(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_trend_strength", name="Trend Strength Score",
        display_name="趋势强度评分", description="基于ADX和价格相对于均线位置，衡量市场趋势的强弱。当ADX低于阈值且价格在均线附近时，认为是regime=unknown的弱趋势状态，因子接近-1；强趋势时接近+1。",
        category="technical", subcategory="trend",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    df = data.copy()
    high = df['high']
    low = df['low']
    close = df['close']
    
    # 计算ADX (14周期)
    tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean()
    
    plus_dm = (high - high.shift()).clip(lower=0)
    minus_dm = (low.shift() - low).clip(lower=0)
    plus_dm[(plus_dm < minus_dm)] = 0
    minus_dm[(minus_dm < plus_dm)] = 0
    plus_di = 100 * plus_dm.rolling(14).mean() / atr
    minus_di = 100 * minus_dm.rolling(14).mean() / atr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di + 1e-10)
    adx = dx.rolling(14).mean()
    
    # 价格相对于20日均线的位置（归一化到0-1）
    ma20 = close.rolling(20).mean()
    price_pos = (close - ma20) / (close.rolling(20).std() + 1e-10)
    price_pos = price_pos.clip(-2, 2) / 2  # 限制在[-1,1]
    
    # 合成因子：趋势强（adx>25且价格远离均线）-> +1；趋势弱（adx<20且价格靠近均线）-> -1
    trend_strength = (adx - 20) / 10  # 将adx 20-30映射到0-1
    trend_strength = trend_strength.clip(-1, 1)
    # 结合价格位置，当价格偏离大时加强趋势信号
    factor = 0.5 * trend_strength + 0.5 * price_pos.sign() * (price_pos.abs() ** 0.5)
    factor = factor.clip(-1, 1)
    return factor
