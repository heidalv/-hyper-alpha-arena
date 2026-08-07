"""AI因子: 波动率收缩突破预期因子 | 置信:60% | 利用布林带带宽收缩和成交量放大的组合，识别即将突破的行情。当带宽（上轨-下轨）/中轨低于20日最低值且成交量高于20日均值的1.5倍时，认为市场可能突破当前无序状态，但方向不确定。通过比较收盘价与布林中轨的位置决定多空方向：突破中轨向上则做多，向下则做空。该因子有助于避免在未知状态中交易，而是等待突破信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilitySqueezeBreakoutAnticipation(BaseFactor):
    """利用布林带带宽收缩和成交量放大的组合，识别即将突破的行情。当带宽（上轨-下轨）/中轨低于20日最低值且成交量高于20日均值的1.5倍时，认为市场可能突破当前无序状态，但方向不确定。通过比较收盘价与布林中轨的位置决定多空方向：突破中轨向上则做多，向下则做空。该因子有助于避免在未知状态中交易，而是等待突破信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_squeeze",
            name="Volatility Squeeze Breakout Anticipation",
            display_name="波动率收缩突破预期因子",
            description="利用布林带带宽收缩和成交量放大的组合，识别即将突破的行情。当带宽（上轨-下轨）/中轨低于20日最低值且成交量高于20日均值的1.5倍时，认为市场可能突破当前无序状态，但方向不确定。通过比较收盘价与布林中轨的位置决定多空方向：突破中轨向上则做多，向下则做空。该因子有助于避免在未知状态中交易，而是等待突破信号。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        import pandas as pd
        close = data['close']
        high = data['high']
        low = data['low']
        volume = data['volume']
    
        # 布林带（20,2）
        ma = close.rolling(20).mean()
        std = close.rolling(20).std()
        upper = ma + 2 * std
        lower = ma - 2 * std
        bandwidth = (upper - lower) / ma
    
        # 带宽最低点（20日最小）
        min_bw = bandwidth.rolling(20).min()
    
        # 成交量均值
        vol_ma = volume.rolling(20).mean()
    
        # 条件：当前带宽接近最低点（小于最低点的1.05倍）且成交量放大1.5倍以上
        cond_band = bandwidth < min_bw * 1.05
        cond_vol = volume > vol_ma * 1.5
        cond = cond_band & cond_vol
    
        # 方向：用收盘价相对于中轨的位置，突破上轨看多（+1），跌破下轨看空（-1），否则0
        direction = np.where(close > upper, 1, np.where(close < lower, -1, 0))
        result = pd.Series(np.where(cond, direction, 0), index=data.index)
        return result
