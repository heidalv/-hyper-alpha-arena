"""AI因子: 清算磁铁反向因子 | 置信:55% | 检测价格接近近期波动区间高低点时，出现放量但未能有效突破，从而产生反向运动的可能性。通过比较当前价格与过去N根K线的高/低点距离以及成交量确认。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class LiquidationMagnetReversal(BaseFactor):
    """检测价格接近近期波动区间高低点时，出现放量但未能有效突破，从而产生反向运动的可能性。通过比较当前价格与过去N根K线的高/低点距离以及成交量确认。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_liq",
            name="Liquidation Magnet Reversal",
            display_name="清算磁铁反向因子",
            description="检测价格接近近期波动区间高低点时，出现放量但未能有效突破，从而产生反向运动的可能性。通过比较当前价格与过去N根K线的高/低点距离以及成交量确认。",
            category="composite",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        df = data.copy()
        n = 30
        df['high_max'] = df['high'].rolling(n).max()
        df['low_min'] = df['low'].rolling(n).min()
        range_ = df['high_max'] - df['low_min'] + 1e-8
        # 价格接近高点或低点的程度 (0到1)
        df['near_high'] = (df['close'] - df['low_min']) / range_
        df['near_low'] = (df['high_max'] - df['close']) / range_
        volume_ma = df['volume'].rolling(n).mean()
        df['vol_ratio'] = df['volume'] / (volume_ma + 1e-8)
        # 接近高点且放量但未创新高 => 看空；接近低点且放量但未创新低 => 看多
        cond_short = (df['near_high'] > 0.8) & (df['vol_ratio'] > 1.5) & (df['close'] < df['high_max'])
        cond_long = (df['near_low'] > 0.8) & (df['vol_ratio'] > 1.5) & (df['close'] > df['low_min'])
        signal = pd.Series(0.0, index=df.index)
        signal.loc[cond_short] = -1.0
        signal.loc[cond_long] = 1.0
        return signal
