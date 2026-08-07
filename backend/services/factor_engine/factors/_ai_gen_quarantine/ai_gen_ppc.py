"""AI因子: 价格位置反转因子 | 置信:60% | 计算当前收盘价在过去N天（20日）最高最低价分位数位置。当价格位于极端高位（>0.8）或极端低位（<0.2）时，因子值接近-1，提示反转风险；当价格处于中间区域时因子值接近+1，表示适合趋势跟随。该因子针对亏损中因追高或突破失败导致的止损和超时问题。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PricePositionContrarian(BaseFactor):
    """计算当前收盘价在过去N天（20日）最高最低价分位数位置。当价格位于极端高位（>0.8）或极端低位（<0.2）时，因子值接近-1，提示反转风险；当价格处于中间区域时因子值接近+1，表示适合趋势跟随。该因子针对亏损中因追高或突破失败导致的止损和超时问题。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_ppc",
            name="Price Position Contrarian",
            display_name="价格位置反转因子",
            description="计算当前收盘价在过去N天（20日）最高最低价分位数位置。当价格位于极端高位（>0.8）或极端低位（<0.2）时，因子值接近-1，提示反转风险；当价格处于中间区域时因子值接近+1，表示适合趋势跟随。该因子针对亏损中因追高或突破失败导致的止损和超时问题。",
            category="behavioral",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        lookback = 20
        rolling_max = data['high'].rolling(lookback).max()
        rolling_min = data['low'].rolling(lookback).min()
        # 避免除以零
        range_ = rolling_max - rolling_min
        range_ = range_.replace(0, np.nan)
        position = (data['close'] - rolling_min) / range_
        # 将position (0~1) 映射到 [-1,1]：中间区域0.4~0.6 -> 1，两端 -> -1
        # 使用对称的抛物线：score = 1 - 4 * (position - 0.5)^2  然后 *2 -1? 不对，需要保持[-1,1]
        # 直接计算：score = 1 - 2 * abs(position - 0.5) * 2? 简化：先算距离d = abs(position-0.5)
        d = (position - 0.5).abs()
        # d范围0~0.5，映射到1~ -1: score = 1 - 4*d
        score = 1.0 - 4.0 * d
        result = score.clip(-1, 1)
        result = result.fillna(0.0)
        return result
