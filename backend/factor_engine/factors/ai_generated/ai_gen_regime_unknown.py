"""AI因子: 未知市场状态风险因子 | 置信:60% | 通过ADX和布林带宽度衡量市场是否处于趋势不明且低波动状态，该状态容易导致逆势开仓或时间止损亏损。ADX低于25且布林带宽度较窄时输出负值，反之输出正值。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class Regime Unknown Risk Factor(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_regime_unknown", name="Regime Unknown Risk Factor",
        display_name="未知市场状态风险因子", description="通过ADX和布林带宽度衡量市场是否处于趋势不明且低波动状态，该状态容易导致逆势开仓或时间止损亏损。ADX低于25且布林带宽度较窄时输出负值，反之输出正值。",
        category="technical", subcategory="trend",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    df = data.copy()
    # 计算ADX (14)
    high = df['high']
    low = df['low']
    close = df['close']
    tr = pd.concat([(high - low).abs(),
                    (high - close.shift()).abs(),
                    (low - close.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean()
    up = high - high.shift()
    down = low.shift() - low
    plus_dm = up.where((up > down) & (up > 0), 0)
    minus_dm = down.where((down > up) & (down > 0), 0)
    plus_di = 100 * (plus_dm.rolling(14).mean() / atr)
    minus_di = 100 * (minus_dm.rolling(14).mean() / atr)
    dx = 100 * ((plus_di - minus_di).abs() / (plus_di + minus_di + 1e-8))
    adx = dx.rolling(14).mean()
    # 布林带宽度 (20,2)
    sma = close.rolling(20).mean()
    std = close.rolling(20).std()
    upper = sma + 2 * std
    lower = sma - 2 * std
    bandwidth = (upper - lower) / (sma + 1e-8)
    # 风险评分: 当ADX<25且bandwidth<0.15时认为未知状态
    low_adx = (adx < 25).astype(float)
    low_band = (bandwidth < 0.15).astype(float)
    risk = low_adx * low_band * (-1)
    # 平滑并映射到[-1,1], 非风险时给出轻度正信号
    smooth_risk = risk.rolling(5, min_periods=1).mean()
    neutral = (1 - smooth_risk.abs()) * 0.3  # 非风险时轻微正偏好
    result = smooth_risk + neutral
    return result.fillna(0).clip(-1, 1)
