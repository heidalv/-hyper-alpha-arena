"""AI因子: 空头动量陷阱因子 | 置信:60% | 识别高开低走且收盘价接近日内低点、成交量放大的形态，此类形态常导致多头止损。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Bearish_Momentum_Trap(BaseFactor):
    """识别高开低走且收盘价接近日内低点、成交量放大的形态，此类形态常导致多头止损。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_bmt",
            name="Bearish Momentum Trap",
            display_name="空头动量陷阱因子",
            description="识别高开低走且收盘价接近日内低点、成交量放大的形态，此类形态常导致多头止损。",
            category="behavioral",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        open = data['open']
        high = data['high']
        low = data['low']
        close = data['close']
        volume = data['volume']
        # 高开低走：开盘价高于昨日收盘价，但收盘价低于开盘价
        gap_up = open > data['close'].shift(1)
        close_below_open = close < open
        # 收盘接近日内低点：收盘价与低点的距离小于振幅的20%
        range_ = high - low
        near_low = (close - low) / range_ < 0.2
        # 成交量放大
        vol_surge = volume > volume.rolling(5).mean() * 1.5
        # 综合信号
        bearish = gap_up & close_below_open & near_low & vol_surge
        signal = -bearish.astype(float) * 1.0
        # 连续形态增强：过去3日出现两次以上加分
        signal = signal + signal.rolling(3).sum().clip(0, 2) * -0.3
        return signal.clip(-1, 1).fillna(0)
