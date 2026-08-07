"""AI因子: 高低点位置与成交量 | 置信:50% | 计算收盘价在当日高低点范围内的相对位置（0~1），再结合成交量变化加权。当收盘靠近高点且成交量放大时为+1，靠近低点且成交量放大时为-1，无量震荡时为0。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class HighLowPositionWithVolume(BaseFactor):
    """计算收盘价在当日高低点范围内的相对位置（0~1），再结合成交量变化加权。当收盘靠近高点且成交量放大时为+1，靠近低点且成交量放大时为-1，无量震荡时为0。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_hlpos",
            name="High-Low Position with Volume",
            display_name="高低点位置与成交量",
            description="计算收盘价在当日高低点范围内的相对位置（0~1），再结合成交量变化加权。当收盘靠近高点且成交量放大时为+1，靠近低点且成交量放大时为-1，无量震荡时为0。",
            category="behavioral",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        high = data['high']
        low = data['low']
        close = data['close']
        volume = data['volume']
        # position in [0,1]     pos = (close - low) / (high - low + 1e-10)
        # volume change ratio (1-day)     vol_ratio = volume / volume.shift(1).replace(0, np.nan)
        vol_ratio = vol_ratio.fillna(1)
        # combine: center around 0.5, then scale     # desire: pos near 1 + high vol_ratio => +1, pos near 0 + high vol_ratio => -1, else near 0     # use pos-0.5, multiply by log(vol_ratio) clip     vol_factor = np.log(vol_ratio).clip(-2, 2)
        raw = (pos - 0.5) * 2 * vol_factor
        result = np.tanh(raw / 2)  # soft clamp
        return result
