"""AI因子: 波动率扩张反转 | 置信:60% | 基于波动率急剧扩张后价格回归均值的现象，类似止损触发和流动性磁铁反转。计算近期ATR与长期ATR比值，当比值超过阈值且价格处于超买/超卖区域时发出反转信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityExpansionReversal(BaseFactor):
    """基于波动率急剧扩张后价格回归均值的现象，类似止损触发和流动性磁铁反转。计算近期ATR与长期ATR比值，当比值超过阈值且价格处于超买/超卖区域时发出反转信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_ver",
            name="Volatility Expansion Reversal",
            display_name="波动率扩张反转",
            description="基于波动率急剧扩张后价格回归均值的现象，类似止损触发和流动性磁铁反转。计算近期ATR与长期ATR比值，当比值超过阈值且价格处于超买/超卖区域时发出反转信号。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        # 参数
        atr_short = 7
        atr_long = 21
        threshold = 1.5
        # 计算ATR
        high, low, close = data['high'], data['low'], data['close']
        tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
        atr_s = tr.rolling(atr_short).mean()
        atr_l = tr.rolling(atr_long).mean()
        atr_ratio = atr_s / atr_l
        # 价格相对位置
        lookback = 20
        highest = high.rolling(lookback).max()
        lowest = low.rolling(lookback).min()
        position = (close - lowest) / (highest - lowest + 1e-10)
        # 信号：波动率扩张且超买/超卖
        overbought = position > 0.8
        oversold = position < 0.2
        signal = np.where(
            (atr_ratio > threshold) & overbought,
            -1.0,
            0.0
        )
        signal = np.where(
            (atr_ratio > threshold) & oversold,
            1.0,
            signal
        )
        # 归一化到[-1,1]
        result = pd.Series(signal, index=data.index)
        return result
