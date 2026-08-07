"""AI因子: 价格区域震荡因子 | 置信:60% | 判断当前价格处于近期波动区间内的位置，结合成交量确认。当价格处于中间区域且成交量萎缩时，市场缺乏方向，容易导致做多亏损。使用过去20日高低点计算相对位置，并乘以成交量相对变化。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PriceZoneOscillator(BaseFactor):
    """判断当前价格处于近期波动区间内的位置，结合成交量确认。当价格处于中间区域且成交量萎缩时，市场缺乏方向，容易导致做多亏损。使用过去20日高低点计算相对位置，并乘以成交量相对变化。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_zone",
            name="Price Zone Oscillator",
            display_name="价格区域震荡因子",
            description="判断当前价格处于近期波动区间内的位置，结合成交量确认。当价格处于中间区域且成交量萎缩时，市场缺乏方向，容易导致做多亏损。使用过去20日高低点计算相对位置，并乘以成交量相对变化。",
            category="composite",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        high = data['high']
        low = data['low']
        close = data['close']
        volume = data['volume']
        period = 20
        hh = high.rolling(period).max()
        ll = low.rolling(period).min()
        range_ = hh - ll
        pos = (close - ll) / (range_ + 1e-10)  # 0~1，中间值为0.5
        # 计算成交量相对变化（近期平均/长期平均）
        vol_short = volume.rolling(10).mean()
        vol_long = volume.rolling(30).mean()
        vol_ratio = vol_short / (vol_long + 1e-10)
        # 当价格位于0.3~0.7中间区域且成交量萎缩（vol_ratio<0.8）时因子值为正（警示）
        zone_signal = ((pos > 0.3) & (pos < 0.7)).astype(float) * (1 - vol_ratio.clip(0, 1))
        return zone_signal * 2 - 1  # 映射到[-1,1]
