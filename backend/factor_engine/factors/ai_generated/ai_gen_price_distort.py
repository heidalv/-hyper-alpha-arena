"""AI因子: 价格扭曲综合征 | 置信:55% | 结合开盘价与日内极值的关系，若收盘价接近日内最低（low）且开盘价接近日内最高（high），或反比，则可能存在人为操控或极端情绪，导致"未知状态"下亏损。计算日内位置不对称性：((close-low)-(high-open))/(high-low)，再取绝对值，当超过阈值0.8时输出-1，否则+1。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class Price Distortion Syndrome(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_price_distort", name="Price Distortion Syndrome",
        display_name="价格扭曲综合征", description="结合开盘价与日内极值的关系，若收盘价接近日内最低（low）且开盘价接近日内最高（high），或反比，则可能存在人为操控或极端情绪，导致"未知状态"下亏损。计算日内位置不对称性：((close-low)-(high-open))/(high-low)，再取绝对值，当超过阈值0.8时输出-1，否则+1。",
        category="behavioral", subcategory="contrarian",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    import numpy as np
    high = data['high']
    low = data['low']
    open_ = data['open']
    close = data['close']
    range_ = high - low
    # 避免除以零
    range_safe = range_.replace(0, np.nan)
    asymmetry = (close - low) - (high - open_)
    asymmetry_norm = asymmetry / range_safe
    distort = asymmetry_norm.abs()
    # 阈值0.8
    result = pd.Series(np.where(distort > 0.8, -1, 1), index=data.index)
    result = result.fillna(1)
    return result
