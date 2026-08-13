"""AI因子: 高波动反转 | 置信:60% | 当波动率在近期显著上升且价格处于高位时，后续价格容易向均值回归，产生下跌。该因子度量过去N日波动率扩张与价格相对位置的综合信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class High_Volatility_Mean_Reversion(BaseFactor):
    """当波动率在近期显著上升且价格处于高位时，后续价格容易向均值回归，产生下跌。该因子度量过去N日波动率扩张与价格相对位置的综合信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_high_vol_rev",
            name="High Volatility Mean Reversion",
            display_name="高波动反转",
            description="当波动率在近期显著上升且价格处于高位时，后续价格容易向均值回归，产生下跌。该因子度量过去N日波动率扩张与价格相对位置的综合信号。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        high = data['high']
        low = data['low']
        close = data['close']
        # 计算ATR作为波动率
        tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
        atr = tr.rolling(14).mean()
        atr_ratio = atr / atr.rolling(30).mean() - 1  # 波动率扩张程度
        # 价格相对位置 (当前价格在近期区间位置)
        lookback = 20
        highest = high.rolling(lookback).max()
        lowest = low.rolling(lookback).min()
        position = (close - lowest) / (highest - lowest)
        # 高波动+高位 => 看跌
        signal = -atr_ratio * position
        result = signal.clip(-1, 1)
        return result.fillna(0)
