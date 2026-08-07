"""AI因子: 量增价不涨 | 置信:60% | 检测成交量突然放大（如较20日均量增加2倍）但价格涨幅很小（<1%）的情况。此模式常预示多头陷阱或空头陷阱，即主力出货或诱空，可能导致微小盈利平仓亏损。因子接近+1表示高危险，接近-1表示有效放量突破。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class Volume Surge Ineffective Breakout(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_vol_surge", name="Volume Surge Ineffective Breakout",
        display_name="量增价不涨", description="检测成交量突然放大（如较20日均量增加2倍）但价格涨幅很小（<1%）的情况。此模式常预示多头陷阱或空头陷阱，即主力出货或诱空，可能导致微小盈利平仓亏损。因子接近+1表示高危险，接近-1表示有效放量突破。",
        category="composite", subcategory="volume",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    close = data['close']
    volume = data['volume']
    # 成交量倍数基准
    vol_ma = volume.rolling(20).mean()
    vol_ratio = volume / vol_ma
    # 价格变化率（1根K线）
    pct_change = close.pct_change()
    # 条件：成交量放大至2倍以上且价格变化绝对值小于1%
    surge = (vol_ratio > 2.0) & (np.abs(pct_change) < 0.01)
    # 同时考虑方向：若放量但价格微涨（false breakout up），则正信号；若放量微跌，则负信号（做空风险）
    signal = surge.astype(float) * np.sign(pct_change).fillna(0)
    # 平滑并映射到[-1,1]
    result = signal.rolling(5, min_periods=1).mean().fillna(0)
    result = np.clip(result, -1, 1)
    return pd.Series(result, index=data.index)
